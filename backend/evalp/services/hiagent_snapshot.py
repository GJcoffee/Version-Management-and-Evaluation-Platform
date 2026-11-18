"""把 HiAgent 已发布的原生导出整理为可入库的版本资产。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from evalp.errors import ValidationFailed
from evalp.runtime.events import REDACTED, is_sensitive_key

PACKAGE_LIMIT = 256 * 1024 * 1024
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
DEPENDENCIES = {
    "SkillIDs": ("--skill-id", "SkillMap"),
    "WorkflowIDs": ("--workflow-id", "WorkflowMap"),
    "ToolIDs": ("--plugin-id", "PluginMap"),
    "PluginIDs": ("--plugin-id", "PluginMap"),
    "KnowledgeIDs": ("--knowledge-id", "KnowledgeMap"),
    "DatabaseIDs": ("--database-id", "DatabaseMap"),
    "QADatasetIDs": ("--qa-dataset-id", "QADataSetMap"),
    "TerminologyIDs": ("--term-dataset-id", "TermDatasetMap"),
    "GraphIDs": ("--graph-id", "GraphMap"),
    "DataSourceIDs": ("--data-source-id", "DataSourceMap"),
}
STRUCTURED = frozenset({".json", ".yaml", ".yml"})
SENSITIVE_FIELDS = re.compile(
    r"(?:apikey|accesskeyid|accesskeysecret|secretaccesskey|password|privatekey|clientsecret|"
    r"authtoken|accesstoken|refreshtoken|authorization|cookie|credentials|faketoken|credential)$",
    re.I,
)
LITERAL_SECRET = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|LTAI[A-Za-z0-9]{16,}|HIAK[A-Za-z0-9+/=]{20,}|sk-[A-Za-z0-9_-]{24,})\b"
    r"|(?i:\bBearer[ \t]+)[A-Za-z0-9._~+/=-]{24,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
)


@dataclass(frozen=True)
class SnapshotSource:
    workspace_id: str
    app_id: str
    publish_id: str


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    kind: str
    data: bytes
    content_type: str | None = None
    source_id: str | None = None
    display_name: str | None = None


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _secret_field(key: str) -> bool:
    return is_sensitive_key(key) or bool(SENSITIVE_FIELDS.search(re.sub(r"[^A-Za-z0-9]", "", key)))


def _clean_text(text: str) -> str:
    return LITERAL_SECRET.sub(REDACTED, text)


def _sanitize(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> Any:
    if budget is None:
        budget = [200_000]
    budget[0] -= 1
    if depth > 64 or budget[0] < 0:
        raise ValidationFailed("HiAgent 导出配置的嵌套结构过大")
    if isinstance(value, dict):
        normalized = {str(k).lower().replace("_", ""): v for k, v in value.items()}
        secret = normalized.get("issensitive") is True or normalized.get("issecret") is True
        secret = secret or any(
            _secret_field(str(normalized.get(k, ""))) for k in ("name", "key", "variablename")
        )
        return {
            str(k): REDACTED
            if v not in (None, "")
            and (
                _secret_field(str(k))
                or (
                    secret
                    and str(k).lower().replace("_", "")
                    in {"value", "default", "defaultvalue", "inputvalue", "content"}
                )
            )
            else _sanitize(v, depth=depth + 1, budget=budget)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(v, depth=depth + 1, budget=budget) for v in value]
    return _clean_text(value) if isinstance(value, str) else value


def _load(data: bytes) -> Any:
    try:
        return yaml.safe_load(data)
    except (yaml.YAMLError, ValueError, RecursionError):
        raise ValidationFailed("无法读取 HiAgent 导出包中的结构化配置") from None


def _json(value: Any) -> bytes:
    return (
        json.dumps(_sanitize(value), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    ).encode()


def _entries(package: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries: dict[str, zipfile.ZipInfo] = {}
    files = package.infolist()
    if len(files) > 10_000 or sum(x.file_size for x in files) > PACKAGE_LIMIT:
        raise ValidationFailed("HiAgent 导出包超出可导入大小或文件数量")
    for item in files:
        path = PurePosixPath(item.filename)
        if (
            path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or "\\" in item.filename
            or ":" in item.filename
            or (item.external_attr >> 16) & 0o170000 == 0o120000
        ):
            raise ValidationFailed("HiAgent 导出包含不安全的文件路径")
        if item.is_dir():
            continue
        name = path.as_posix()
        if name in entries:
            raise ValidationFailed("HiAgent 导出包中存在重复的文件路径")
        entries[name] = item
    return entries


def _skip_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        name == ".env"
        or (name.startswith(".env.") and name not in {".env.example", ".env.sample"})
        or name in {"id_rsa", "id_ed25519", "auth.json", "credentials.json", ".npmrc", ".netrc"}
        or PurePosixPath(name).suffix in {".pem", ".key", ".p12", ".pfx"}
    )


def _clean_file(path: str, data: bytes, *, depth: int) -> bytes:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".zip":
        return _clean_zip(data, depth=depth + 1)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        if suffix in STRUCTURED:
            raise ValidationFailed("HiAgent 导出的结构化文件不是有效文本") from None
        return data
    if suffix in STRUCTURED:
        value = _load(data)
        cleaned = _sanitize(value)
        if cleaned != value:
            return (
                yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False).encode()
                if suffix != ".json"
                else _json(cleaned)
            )
    cleaned_text = _clean_text(text)
    if cleaned_text != text:
        # 脚本或业务文档内嵌的密钥需要先在源端移除，避免静默改变代码。
        name = _clean_text(PurePosixPath(path).name)
        raise ValidationFailed(f"导出文件 {name} 包含疑似明文凭据，请从 HiAgent 移除后重新导入")
    return data


def _clean_zip(data: bytes, *, depth: int = 0, skill: bool = False) -> bytes:
    if depth > 2:
        raise ValidationFailed("HiAgent 依赖包包含过深的嵌套压缩文件")
    output = io.BytesIO()
    changed = False
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        entries = _entries(source)
        if skill and not any(PurePosixPath(name).name == "SKILL.md" for name in entries):
            raise ValidationFailed("HiAgent 导出的技能包缺少 SKILL.md")
        for item in source.infolist():
            if _skip_file(item.filename):
                changed = True
                continue
            raw = source.read(item)
            cleaned = raw if item.is_dir() else _clean_file(item.filename, raw, depth=depth)
            changed |= cleaned != raw
            target.writestr(item.filename, cleaned)
    return output.getvalue() if changed else data


def read_app(package: zipfile.ZipFile, source: SnapshotSource) -> tuple[str, dict]:
    entries = _entries(package)
    candidates = [
        name for name in entries if name.startswith("agent/") and PurePosixPath(name).suffix in STRUCTURED
    ]
    for name in candidates:
        app = _mapping(_load(package.read(entries[name])))
        config = _mapping(app.get("AppConfig"))
        if config.get("AppID") == source.app_id and app.get("VersionCode") == source.publish_id:
            if config.get("WorkspaceID") != source.workspace_id:
                raise ValidationFailed("导出包的工作空间与选择不一致")
            return name, app
    raise ValidationFailed("导出包没有匹配所选应用和发布编号的配置")


def dependency_args(app: dict) -> list[str]:
    ids: set[tuple[str, str]] = set()

    def visit(node: Any, depth: int) -> None:
        if depth > 64:
            raise ValidationFailed("HiAgent 应用配置的嵌套结构过深")
        if isinstance(node, dict):
            for key, value in node.items():
                if key in DEPENDENCIES and isinstance(value, list):
                    ids.update(
                        (DEPENDENCIES[key][0], item)
                        for item in value
                        if isinstance(item, str) and re.fullmatch(ID_PATTERN, item)
                    )
                if isinstance(value, (dict, list)):
                    visit(value, depth + 1)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, (dict, list)):
                    visit(item, depth + 1)

    visit(app.get("AppConfig"), 0)
    depends = _mapping(app.get("AppDepends"))
    for flag, map_key in DEPENDENCIES.values():
        ids.update(
            (flag, key)
            for key in _mapping(depends.get(map_key))
            if isinstance(key, str) and re.fullmatch(ID_PATTERN, key)
        )
    if len(ids) > 200:
        raise ValidationFailed("HiAgent 应用引用的依赖数量超出单次导入上限")
    return [value for pair in sorted(ids) for value in pair]


def _skill_files(
    package: zipfile.ZipFile, app: dict, entries: dict[str, zipfile.ZipInfo]
) -> list[SnapshotFile]:
    depends = _mapping(app.get("AppDepends"))
    skills = _mapping(depends.get("SkillMap"))
    result = []
    flags = dependency_args(app)
    expected = {sid for flag, sid in zip(flags[::2], flags[1::2], strict=True) if flag == "--skill-id"}
    if expected != set(skills):
        raise ValidationFailed("HiAgent 导出缺少应用引用的技能清单")
    for sid, meta in sorted(skills.items()):
        name = _mapping(meta).get("Name")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise ValidationFailed("HiAgent 导出中的技能名称无效")
        member = f"skill/{name}.zip"
        if member not in entries:
            raise ValidationFailed(f"HiAgent 导出缺少技能包：{_clean_text(name)}")
        data = _clean_zip(package.read(entries[member]), skill=True)
        result.append(
            SnapshotFile(f"skills/{name}.zip", "skill", data, "application/zip", sid, _clean_text(name))
        )
    return result


def collect_snapshot(
    archive: Path, extra: list[tuple[str, Path]], source: SnapshotSource
) -> list[SnapshotFile]:
    """保留原始技能文件，对导出配置脱敏，并检查关键依赖是否齐全。"""
    if sum(p.stat().st_size for p in (archive, *(p for _, p in extra))) > PACKAGE_LIMIT:
        raise ValidationFailed("HiAgent 下载的导出文件超出可导入大小")
    try:
        with zipfile.ZipFile(archive) as package:
            app_path, app = read_app(package, source)
            entries = _entries(package)
            files = _skill_files(package, app, entries)
            files.append(
                SnapshotFile("platform/native-application.json", "app_config", _json(app), "application/json")
            )
            config = _mapping(app.get("AppConfig"))
            prompt = _mapping(config.get("SingleAgentConfig")).get("PrePrompt")
            if isinstance(prompt, str) and prompt:
                files.append(
                    SnapshotFile("system-prompt.md", "prompt", _clean_text(prompt).encode(), "text/markdown")
                )

            workflows = _mapping(_mapping(app.get("AppDepends")).get("WorkflowMap"))
            for item in workflows.values():
                name = _mapping(item).get("Name")
                if isinstance(name, str) and not any(
                    path.rsplit(".", 1)[0] == f"workflow/{name}" for path in entries
                ):
                    raise ValidationFailed(f"HiAgent 导出缺少工作流定义：{_clean_text(name)}")
            for path, entry in sorted(entries.items()):
                if path == app_path or path.startswith("skill/") or _skip_file(path):
                    continue
                filename = PurePosixPath(path).name
                if PurePosixPath(path).suffix in STRUCTURED:
                    folder = path.split("/", 1)[0]
                    kind = {"plugin": "mcp", "knowledge": "kb_config", "database": "database"}.get(
                        folder, "app_config"
                    )
                    dest = "platform/native-" + path.replace("/", "-").rsplit(".", 1)[0] + ".json"
                    files.append(
                        SnapshotFile(dest, kind, _json(_load(package.read(entry))), "application/json")
                    )
                else:
                    dest = f"files/{filename}" if path.startswith("asset/") else f"files/{path}"
                    files.append(SnapshotFile(dest, "file", _clean_file(path, package.read(entry), depth=0)))

        for identifier, path in extra:
            filename = path.name
            if not _skip_file(filename):
                data = _clean_file(filename, path.read_bytes(), depth=0)
                files.append(SnapshotFile(f"dependencies/{identifier}/{filename}", "file", data))
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, RecursionError):
        raise ValidationFailed("HiAgent 导出文件不完整或无法读取，请重新导出") from None
    return files
