"""通过本机 hicli 读取发布记录、导出文件并保存 EvalP 版本。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalp.context import AppContext
from evalp.errors import Conflict, EvalpError, NotFound, ValidationFailed
from evalp.runtime.adapters.platforms.hiagent.hicli_observe import HicliObserveError, _command
from evalp.schemas.agent import PushIn, WorkspaceFile, check_agent_name, check_tag
from evalp.services.adapter import AdapterService
from evalp.services.agent import AgentService
from evalp.services.draft import DraftService
from evalp.services.hiagent_snapshot import (
    ID_PATTERN,
    PACKAGE_LIMIT,
    SnapshotFile,
    SnapshotSource,
    collect_snapshot,
    dependency_args,
    read_app,
)
from evalp.services.version import VersionService
from evalp.services.workspace import WorkspaceService
from evalp.spec.refs import format_asset_ref
from evalp.store.models import Agent, Asset, Draft, Version
from evalp.workspace_client import normalize_stem

HiAgentId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
PAGE_SIZE = 50


class HiAgentImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_id: HiAgentId
    app_id: HiAgentId
    publish_id: HiAgentId
    agent_id: str | None = Field(None, pattern=r"^agt_[A-Za-z0-9]+$")
    agent_name: str | None = None
    notes: str = Field(min_length=1, max_length=1000)
    tag: str | None = None

    @field_validator("agent_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return check_agent_name(value) if value is not None else None

    @field_validator("tag")
    @classmethod
    def _tag(cls, value: str | None) -> str | None:
        return check_tag(value) if value is not None else None

    @property
    def source(self) -> SnapshotSource:
        return SnapshotSource(self.workspace_id, self.app_id, self.publish_id)


class HiAgentImportError(EvalpError):
    default_code = "HIAGENT_IMPORT_FAILED"
    default_status = 502


async def _hicli(*args: str, timeout: int = 30) -> dict:
    try:
        command = _command()
        process = await asyncio.create_subprocess_exec(
            *command,
            "app",
            "--json",
            "--timeout",
            "20",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (HicliObserveError, OSError) as exc:
        raise HiAgentImportError("无法启动 hicli，请在运行 EvalP 的电脑上安装并完成 hicli auth 登录") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError) as exc:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HiAgentImportError("HiAgent 请求超时，请检查网络后重试") from None

    if process.returncode:
        details = (stdout + stderr).decode("utf-8", errors="replace").lower()
        if any(word in details for word in ("download", "31011", "31010", "fileurl")):
            message = "HiAgent 导出文件下载失败，请检查 hicli 的文件下载配置和网络"
        elif args[:2] == ("agent", "export"):
            message = "HiAgent 导出失败，请检查应用发布状态、依赖权限与 hicli 登录"
        else:
            message = "无法读取 HiAgent，请检查本机 hicli 登录、工作空间编号和访问权限"
        raise HiAgentImportError(message)
    try:
        response = json.loads(stdout)
        if response.get("ok") is False:
            raise ValueError
        data = response.get("data", response)
        data = data.get("Result", data)
        if not isinstance(data, dict) or data.get("ResponseMetadata", {}).get("Error"):
            raise ValueError
    except (ValueError, AttributeError, UnicodeDecodeError):
        raise HiAgentImportError("hicli 未返回有效的 HiAgent 数据") from None
    return data


async def _download(
    source: SnapshotSource,
    output: Path,
    dependencies: list[str],
) -> tuple[Path, list[tuple[str, Path]]]:
    result = await _hicli(
        "agent",
        "export",
        "--workspace-id",
        source.workspace_id,
        "--app-id",
        source.app_id,
        "--published",
        "--publish-id",
        source.publish_id,
        "--output-dir",
        str(output),
        "--timeout",
        "180",
        *dependencies,
        timeout=240,
    )
    downloads = result.get("downloads")
    if not isinstance(downloads, list):
        raise HiAgentImportError("hicli 未返回可读取的导出文件")
    primary: list[Path] = []
    extra: list[tuple[str, Path]] = []
    for row in downloads:
        if not isinstance(row, dict) or not isinstance(row.get("output"), str):
            raise HiAgentImportError("hicli 返回了无效的导出文件记录")
        path = Path(row["output"]).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            raise HiAgentImportError("hicli 导出文件缺失或下载目录不一致")
        if str(row.get("component_type", "")).lower() == "app":
            primary.append(path)
        else:
            identifier = str(row.get("id") or path.stem)
            if not re.fullmatch(ID_PATTERN, identifier):
                raise HiAgentImportError("hicli 返回了无效的依赖编号")
            extra.append((identifier, path))
    if len(primary) != 1 or primary[0].stat().st_size > PACKAGE_LIMIT:
        raise HiAgentImportError("应用导出包数量或大小异常")
    return primary[0], extra


def _dependencies(archive: Path, source: SnapshotSource) -> list[str]:
    try:
        with zipfile.ZipFile(archive) as package:
            _, app = read_app(package, source)
            return dependency_args(app)
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError):
        raise HiAgentImportError("HiAgent 应用导出包不完整") from None


async def _export(source: SnapshotSource) -> list[SnapshotFile]:
    with tempfile.TemporaryDirectory(prefix="evalp-hiagent-import-") as directory:
        root = Path(directory)
        archive, extra = await _download(source, root / "configuration", [])
        dependencies = await asyncio.to_thread(_dependencies, archive, source)
        if dependencies:
            archive, extra = await _download(source, root / "full", dependencies)
        return await asyncio.to_thread(collect_snapshot, archive, extra, source)


def _page(response: dict, page: int) -> tuple[list[dict], bool]:
    rows = response.get("Items")
    if rows is None:
        rows = []
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise HiAgentImportError("HiAgent 列表返回了不支持的数据格式")
    total = response.get("Total")
    more = page * PAGE_SIZE < total if isinstance(total, int) else len(rows) == PAGE_SIZE
    return rows, more


class HiAgentImportService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._import_lock = asyncio.Lock()

    async def apps(self, workspace_id: str, page: int) -> dict:
        data = await _hicli(
            "agent",
            "list",
            "--workspace-id",
            workspace_id,
            "--published",
            "--app-type",
            "Chat",
            "--page-number",
            str(page),
            "--page-size",
            str(PAGE_SIZE),
        )
        rows, more = _page(data, page)
        return {
            "items": [
                {"id": row["ID"], "name": row.get("Name") or row["ID"]}
                for row in rows
                if row.get("AppType") == "Chat" and row.get("IsPublished") and row.get("ID")
            ],
            "more": more,
        }

    async def releases(self, workspace_id: str, app_id: str, page: int) -> dict:
        data = await _hicli(
            "publish",
            "history",
            "--workspace-id",
            workspace_id,
            "--app-id",
            app_id,
            "--page-number",
            str(page),
            "--page-size",
            str(PAGE_SIZE),
        )
        rows, more = _page(data, page)
        items = {
            row["PublishID"]: {"id": row["PublishID"], "timestamp": row.get("PublishTimestamp")}
            for row in reversed(rows)
            if row.get("PublishID")
        }
        return {"items": list(reversed(items.values())), "more": more}

    async def _identity(self, version: Version) -> tuple[str, str] | None:
        spec = version.spec
        if spec.get("transport") != "adapter" or spec.get("platform") not in (None, "hiagent"):
            return None
        params = spec.get("params") or {}
        if params.get("hiagent_workspace_id") and params.get("hiagent_app_id"):
            return params["hiagent_workspace_id"], params["hiagent_app_id"]
        # 兼容命令行导入的旧版本，从不可变的来源清单识别应用。
        pins = (spec.get("snapshot") or {}).get("files") or {}
        pin = (pins.get("platform/source.json") or {}).get("$asset", {})
        if not pin.get("hash"):
            return None
        try:
            meta = json.loads(await self.ctx.blobs.get_bytes(pin["hash"]))
        except (NotFound, OSError, ValueError):
            return None
        if isinstance(meta, dict) and meta.get("workspace_id") and meta.get("app_id"):
            return meta["workspace_id"], meta["app_id"]
        return None

    async def _versions(self, session: AsyncSession, agent_id: str) -> list[Version]:
        return list(
            (
                await session.scalars(
                    select(Version).where(Version.agent_id == agent_id).order_by(Version.seq.desc())
                )
            ).all()
        )

    async def context(self, agent_id: str | None) -> dict:
        if agent_id:
            async with self.ctx.db.read() as session:
                agent = await AgentService.get_orm(session, agent_id)
                for version in await self._versions(session, agent.id):
                    identity = await self._identity(version)
                    if identity is not None:
                        return {"workspace_id": identity[0], "app_id": identity[1]}
            raise ValidationFailed("未找到该智能体的 HiAgent 来源记录，无法确认要追加版本的应用")
        environments = await self.ctx.svc(AdapterService).get()
        for environment in ("test", "production"):
            binding = environments[environment].get("binding")
            if binding and binding["config"].get("workspace_id"):
                return {"workspace_id": binding["config"]["workspace_id"], "app_id": None}
        return {"workspace_id": None, "app_id": None}

    async def _target(
        self,
        session: AsyncSession,
        request: HiAgentImportRequest,
    ) -> tuple[Agent | None, Version | None, Version | None]:
        agent = await AgentService.find_orm(session, request.agent_id or request.agent_name or "")
        if not agent:
            if request.agent_id:
                raise ValidationFailed("要追加版本的智能体不存在")
            return None, None, None
        if agent.kind != "code" or agent.archived or (request.agent_id and agent.id != request.agent_id):
            raise Conflict("目标智能体不可导入 HiAgent 版本")
        versions = await self._versions(session, agent.id)
        identity = (request.workspace_id, request.app_id)
        previous = None
        for version in versions:
            if await self._identity(version) != identity:
                continue
            previous = previous or version
            if (version.spec.get("params") or {}).get("hiagent_publish_id") == request.publish_id:
                return agent, previous, version
        if not request.agent_id:
            raise Conflict("该智能体名称已存在，请使用该智能体的「从 HiAgent 导入版本」，或换一个名称")
        if previous is None:
            raise Conflict("选中的工作空间和应用与该智能体的来源不一致")
        return agent, previous, None

    async def _build_push(
        self,
        session: AsyncSession,
        request: HiAgentImportRequest,
        agent: Agent | None,
        previous: Version | None,
        files: list[SnapshotFile],
        draft_name: str,
        revision: int | None,
    ) -> PushIn:
        name = agent.name if agent is not None else request.agent_name
        assert name is not None
        source_data = json.dumps(
            {
                "workspace_id": request.workspace_id,
                "app_id": request.app_id,
                "publish_id": request.publish_id,
                "name": name,
                "complete": True,
                "export_method": "hicli",
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode()
        files = [*files, SnapshotFile("platform/source.json", "app_config", source_data, "application/json")]
        parent_spec = previous.spec if previous is not None else {}
        old_pins = (parent_spec.get("snapshot") or {}).get("files") or {}
        source_ids = [file.source_id for file in files if file.source_id]
        existing = (await session.scalars(select(Asset).where(Asset.source_id.in_(source_ids)))).all()
        names_by_source = {(a.kind, a.source_id): a.name for a in existing}
        workspace_files = []
        rules = []
        refs = {}
        for file in files:
            pin = (old_pins.get(file.path) or {}).get("$asset", {})
            known = names_by_source.get((file.kind, file.source_id))
            if not file.source_id and pin.get("kind") == file.kind:
                known = pin.get("name")
            stem = normalize_stem(PurePosixPath(file.path).stem)[:27].strip("-") or file.kind.replace(
                "_", "-"
            )
            identity = "|".join((request.workspace_id, request.app_id, file.source_id or file.path))
            suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
            asset_name = known or f"hiagent-{stem}-{suffix}"
            rule = {"glob": file.path, "kind": file.kind, "name": asset_name}
            if file.source_id:
                rule["id"] = file.source_id
            rules.append(rule)
            refs[file.path] = format_asset_ref(file.kind, asset_name)
            workspace_files.append(
                WorkspaceFile(
                    path=file.path,
                    asset=f"{file.kind}/{asset_name}",
                    hash=hashlib.sha256(file.data).hexdigest(),
                    content=base64.b64encode(file.data).decode("ascii"),
                    encoding="base64",
                    origin="snapshot",
                    source_id=file.source_id,
                    display_name=file.display_name,
                    content_type=file.content_type,
                )
            )
        spec = {
            "schema": "evalp/v1",
            "kind": "code",
            "name": name,
            "transport": "adapter",
            "platform": "hiagent",
            "source": {"capture_git": False},
            "runtime": parent_spec.get("runtime") or {"turn_timeout_s": 900},
            "params": {
                **(parent_spec.get("params") or {}),
                "hiagent_app_id": request.app_id,
                "hiagent_workspace_id": request.workspace_id,
                "hiagent_publish_id": request.publish_id,
                "snapshot_complete": True,
            },
            "snapshot": {"dir": "exports/current", "map": rules, "files": refs},
        }
        return PushIn(
            agent_name=name,
            agent_kind="code",
            draft_name=draft_name,
            spec=spec,
            files=workspace_files,
            expected_revision=revision,
        )

    async def save(self, request: HiAgentImportRequest) -> dict:
        if bool(request.agent_id) == bool(request.agent_name):
            raise ValidationFailed("请选择新建智能体或追加到已有智能体")
        async with self._import_lock:
            async with self.ctx.db.read() as session:
                _, _, duplicate = await self._target(session, request)
                if duplicate is not None:
                    return await self._result(session, duplicate, existed=True)
            files = await _export(request.source)
            async with self.ctx.db.write() as session:
                agent, previous, duplicate = await self._target(session, request)
                if duplicate is not None:
                    return await self._result(session, duplicate, existed=True)
                draft_name = "hiagent-import"
                old_draft = await DraftService.find_by_name(session, agent.id, draft_name) if agent else None
                push = await self._build_push(
                    session,
                    request,
                    agent,
                    previous,
                    files,
                    draft_name,
                    old_draft.revision if old_draft else None,
                )
                out = await self.ctx.svc(WorkspaceService).push(push, session=session)
                draft = await session.get(Draft, out.draft.id)
                assert draft is not None
                if previous is not None:
                    draft.base_version_id = previous.id
                main = None
                if out.agent_created:
                    created = await session.get(Agent, out.agent_id)
                    assert created is not None
                    created.conventions = {
                        "require_notes": True,
                        "require_source_id": ["skill"],
                        "snapshot_readonly": True,
                    }
                    main = await DraftService.find_by_name(session, out.agent_id, "main")
                    assert main is not None
                    main.spec = draft.spec
                result = await self.ctx.svc(DraftService).publish(
                    draft.id,
                    draft.revision,
                    notes=request.notes,
                    tag=request.tag,
                    via="ui",
                    session=session,
                )
                if main is not None:
                    main.base_version_id = result.version.id
                return {
                    "version": result.version.model_dump(exclude={"spec", "assets"}),
                    "existed": result.existed,
                    "asset_count": len(result.version.assets),
                }

    async def _result(self, session: AsyncSession, version: Version, *, existed: bool) -> dict:
        dto = await self.ctx.svc(VersionService).out_in_session(session, version)
        return {
            "version": dto.model_dump(exclude={"spec", "assets"}),
            "existed": existed,
            "asset_count": len(dto.assets),
        }
