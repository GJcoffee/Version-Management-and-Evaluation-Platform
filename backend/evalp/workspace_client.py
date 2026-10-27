"""工作区同步客户端（详设 10）：``.evalp.toml``、``.evalp/state.json``、push / pull。

服务端在一个事务内完成资产修订、``file:`` 引用改写和草稿保存（``POST /workspace/push``）；
本模块负责读取本地文件、按哈希只上传有变化的文件、处理冲突，以及把 pull 的结果写回本地。
"""

from __future__ import annotations

import base64
import copy
import fnmatch
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from evalp.client import EvalpClient, EvalpClientError
from evalp.spec.models import SnapshotRule, format_validation_errors
from evalp.spec.refs import RefError, validate_asset_name
from evalp.yamlio import dump_preserving, plain_dump

CONFIG_NAME = ".evalp.toml"
STATE_DIR = ".evalp"
STATE_NAME = "state.json"
FILE_PREFIX = "file:"
_GLOB_CHARS = set("*?[")


class WorkspaceError(Exception):
    """工作区相关的本地错误（配置缺失、文件不存在等），消息面向用户。"""


class ProjectNotFound(WorkspaceError):
    def __init__(self, start: Path) -> None:
        super().__init__(
            f"在 {start} 及其上级目录中找不到 {CONFIG_NAME}。请先在 agent 项目目录运行 evalp init。"
        )


class PushConflict(WorkspaceError):
    """草稿在服务端被修改过（DRAFT_CONFLICT）。``diff`` 左侧是服务端、右侧是本地。"""

    def __init__(
        self, message: str, current_revision: int | None, diff: dict | None, server_spec: dict | None
    ):
        super().__init__(message)
        self.current_revision = current_revision
        self.diff = diff or {}
        self.server_spec = server_spec or {}


# ---------------------------------------------------------------------- 项目配置


EVAL_DEFAULT_KEYS = ("dataset", "evaluators", "repeats", "baseline", "versions", "concurrency")


@dataclass
class ProjectConfig:
    """``.evalp.toml`` 的内容。"""

    root: Path
    agent_name: str
    spec_path: str = "agent.yaml"
    draft_name: str = "main"
    server_url: str | None = None
    assets: dict[str, str] = field(default_factory=dict)
    eval: dict[str, Any] = field(default_factory=dict)  # [eval] 默认评测配置（已校验类型）
    mcp_autostart: bool = True  # [mcp].autostart：MCP 启动时是否自动拉起 evalp serve

    @property
    def spec_file(self) -> Path:
        return self.root / self.spec_path

    @property
    def state_file(self) -> Path:
        return self.root / STATE_DIR / STATE_NAME


def find_project(start: str | os.PathLike[str] | None = None) -> ProjectConfig:
    """从 start（默认当前目录）向上查找 ``.evalp.toml``。"""
    here = Path(start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_NAME).is_file():
            return load_project(d)
    raise ProjectNotFound(here)


def load_project(root: Path) -> ProjectConfig:
    path = root / CONFIG_NAME
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise WorkspaceError(f"{path} 不是合法的 TOML: {e}") from e
    agent = raw.get("agent") or {}
    name = agent.get("name")
    if not name:
        raise WorkspaceError(f"{path} 缺少 [agent].name")
    assets = {str(k): str(v) for k, v in (raw.get("assets") or {}).items()}
    mcp_section = raw.get("mcp") or {}
    if not isinstance(mcp_section, dict) or not isinstance(mcp_section.get("autostart", True), bool):
        raise WorkspaceError(f"{path} 的 [mcp].autostart 应为 true / false。")
    return ProjectConfig(
        root=root,
        agent_name=str(name),
        spec_path=str(agent.get("spec", "agent.yaml")),
        draft_name=str(agent.get("draft", "main")),
        server_url=(raw.get("server") or {}).get("url"),
        assets=assets,
        eval=_load_eval_defaults(raw.get("eval"), path),
        mcp_autostart=mcp_section.get("autostart", True),
    )


def _load_eval_defaults(section: Any, path: Path) -> dict[str, Any]:
    """校验 ``[eval]`` 段：只接受已知键且类型正确，写错时给出明确提示而不是静默忽略。"""
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise WorkspaceError(f"{path} 的 [eval] 应是一个表。")
    unknown = sorted(set(section) - set(EVAL_DEFAULT_KEYS))
    if unknown:
        raise WorkspaceError(
            f"{path} 的 [eval] 含有未知配置: {', '.join(unknown)}（可用: {', '.join(EVAL_DEFAULT_KEYS)}）"
        )
    out: dict[str, Any] = {}
    for key, value in section.items():
        if key in ("dataset", "baseline"):
            ok = isinstance(value, str) and bool(value.strip())
        elif key in ("repeats", "concurrency"):
            ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif key == "versions":
            ok = (isinstance(value, str) and bool(value.strip())) or (
                isinstance(value, list)
                and bool(value)
                and all(isinstance(v, str) and v.strip() for v in value)
            )
            if ok and isinstance(value, str):
                value = [v.strip() for v in value.split(",") if v.strip()]
        else:  # evaluators：文件路径，或评估器表数组
            ok = (isinstance(value, str) and bool(value.strip())) or (
                isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)
            )
        if not ok:
            hint = {
                "dataset": "非空字符串",
                "baseline": "非空字符串",
                "repeats": "不小于 1 的整数",
                "concurrency": "不小于 1 的整数",
                "versions": "版本字符串（逗号分隔）或字符串数组",
                "evaluators": "评估器文件路径，或 [[eval.evaluators]] 表数组",
            }[key]
            raise WorkspaceError(f"{path} 的 [eval].{key} 应是{hint}。")
        out[key] = value
    return out


# ---------------------------------------------------------------------- 本地状态


@dataclass
class State:
    agent_id: str | None = None
    draft_id: str | None = None
    draft_revision: int | None = None
    spec_hash: str | None = None
    files: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> State:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(
            agent_id=raw.get("agent_id"),
            draft_id=raw.get("draft_id"),
            draft_revision=raw.get("draft_revision"),
            spec_hash=raw.get("spec_hash"),
            files=dict(raw.get("files") or {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.__dict__, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)


# ---------------------------------------------------------------------- 文件与引用


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def spec_digest(spec: dict[str, Any]) -> str:
    return sha256_bytes(json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))


def normalize_stem(stem: str) -> str:
    """``{stem}`` 展开为合法的资产名：小写，非字母数字替换为连字符。"""
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")


def iter_file_refs(spec: dict[str, Any]) -> list[str]:
    """spec 中引用位置上的 ``file:`` 路径（与服务端改写的位置一致）。"""
    out: list[str] = []

    def one(v: Any) -> None:
        if isinstance(v, str) and v.startswith(FILE_PREFIX):
            out.append(norm_path(v[len(FILE_PREFIX) :]))

    prompts = spec.get("prompts")
    if isinstance(prompts, dict):
        one(prompts.get("system"))
    for tool in spec.get("tools") or []:
        if isinstance(tool, dict):
            w = tool.get("with")
            for s in (w.get("sources") or []) if isinstance(w, dict) else []:
                one(s)
        else:
            one(tool)
    knowledge = spec.get("knowledge")
    if isinstance(knowledge, dict):
        for k in knowledge.get("inject") or []:
            one(k)
    return out


def norm_path(path: str) -> str:
    p = os.path.normpath(path.strip().replace("\\", "/")).replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def snapshot_asset(agent_name: str, rel: str) -> str:
    """快照目录内某个文件对应的资产 ``file/snap-{agent}-{路径}-{路径哈希}``（名字合法且不超过 64 字符）。"""
    parts = (
        "snap",
        normalize_stem(agent_name)[:20].strip("-"),
        normalize_stem(rel)[:30].strip("-"),
        hashlib.sha1(rel.encode("utf-8")).hexdigest()[:6],
    )
    return "file/" + "-".join(p for p in parts if p)


def _has_glob(pattern: str) -> bool:
    return any(c in pattern for c in _GLOB_CHARS)


@lru_cache(maxsize=128)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    """glob -> 正则：``*`` / ``?`` 不跨目录，``**`` 跨目录（``**/`` 可匹配零层目录）。"""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        c = pattern[i]
        close = pattern.find("]", i + 2) if c == "[" else -1
        if close != -1:
            body = pattern[i + 1 : close]
            out.append("[" + ("^" + body[1:] if body[0] in "!^" else body) + "]")
            i = close
        elif c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))


def glob_match(pattern: str, rel: str) -> bool:
    """``rel``（/ 分隔的相对路径）是否匹配 glob。"""
    return _glob_regex(pattern).fullmatch(rel) is not None


_VERSION_SUFFIX = re.compile(r"-(?:v\d+(?:\.\d+)*|\d+(?:\.\d+)+)$", re.IGNORECASE)
_TEMPLATE_VAR = re.compile(r"\{(\w+)\}")
_TEMPLATE_VARS = ("stem", "stem_nover", "agent", "path")


def strip_version(stem: str) -> str:
    """去掉文件名末尾的版本号：``crawl-engine-0.5.3`` / ``crawl-engine-v1.2`` -> ``crawl-engine``。"""
    return _VERSION_SUFFIX.sub("", stem)


@dataclass(frozen=True)
class SnapshotEntry:
    """快照目录内一个文件对应的资产：``kind/name``，以及可选的来源 ID 与原始显示名。"""

    asset: str
    source_id: str | None = None
    display_name: str | None = None


def _json_value(data: bytes, path: str, rel: str, rule: SnapshotRule, field: str) -> str:
    """按 ``$.a.b.0`` 点路径从 JSON 文件内容取值（对象按键、数组按下标），要求取到非空的字符串或数字。"""
    where = f"文件 {rel} 按规则 {{glob: {rule.glob}, {field}: {path}}}"
    try:
        node: Any = json.loads(data)
    except ValueError:
        raise WorkspaceError(f"{where} 取值失败: 文件不是合法的 JSON") from None
    for seg in path[2:].split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
            node = node[int(seg)]
        else:
            raise WorkspaceError(f"{where} 取值失败: 找不到 {seg!r}。请按实际导出的 JSON 调整字段路径。")
    if isinstance(node, bool) or not isinstance(node, str | int | float) or not str(node).strip():
        raise WorkspaceError(f"{where} 取值失败: 取到的不是非空的字符串或数字（{node!r}）")
    return str(node).strip()


def snapshot_rule_entry(agent_name: str, rule: SnapshotRule, rel: str, data: bytes) -> SnapshotEntry:
    """按规则展开快照文件 ``rel``（内容 ``data``）的资产 ``kind/name``、来源 ID 与显示名。

    ``name`` / ``id`` / ``display`` 以 ``$.`` 开头时从 JSON 内容取值，否则按模板展开。
    资产名由 ``name`` 的值规范化得到（小写，非字母数字替换为连字符）；显示名取 ``display``，
    省略时取 ``name`` 的原值；``id`` 保持原样。展开结果不合法时抛 WorkspaceError。
    """
    p = Path(rel)
    values = {
        "stem": p.stem,
        "stem_nover": strip_version(p.stem),
        "agent": agent_name,
        "path": p.with_suffix("").as_posix(),
    }

    def expand(m: re.Match[str]) -> str:
        if m.group(1) not in values:
            raise WorkspaceError(
                f"snapshot.map 的模板 {rule.name!r} 里有未知变量 {m.group(0)}"
                f"（可用: {', '.join('{' + v + '}' for v in _TEMPLATE_VARS)}）"
            )
        return values[m.group(1)]

    def evaluate(value: str, field: str) -> str:
        if value.startswith("$."):
            return _json_value(data, value, rel, rule, field)
        return _TEMPLATE_VAR.sub(expand, value)

    display = evaluate(rule.name, "name")
    name = normalize_stem(display)
    try:
        validate_asset_name(name)
    except RefError as e:
        raise WorkspaceError(
            f"文件 {rel} 按规则 {{glob: {rule.glob}, name: {rule.name}}} 得到的资产名不合法"
            f"（展开结果 {display!r}）: {e}"
        ) from None
    source_id = evaluate(rule.id, "id") if rule.id else None
    if rule.display:
        display = evaluate(rule.display, "display")
    return SnapshotEntry(f"{rule.kind}/{name}", source_id, display)


def snapshot_rule_asset(agent_name: str, rule: SnapshotRule, rel: str, data: bytes = b"") -> str:
    """同 :func:`snapshot_rule_entry`，只返回 ``kind/name``（``$.`` 取值的规则需要传文件内容）。"""
    return snapshot_rule_entry(agent_name, rule, rel, data).asset


_CONTENT_TYPES = {
    "json": "application/json",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "yaml": "application/x-yaml",
    "yml": "application/x-yaml",
    "txt": "text/plain",
    "jsonl": "application/jsonl",
    "csv": "text/csv",
}


def _asset_name(template: str, stem: str) -> str:
    return template.replace("{stem}", normalize_stem(stem))


@dataclass
class LocalFile:
    path: str  # 相对项目根的 / 路径
    asset: str  # kind/name
    data: bytes
    hash: str
    origin: str | None = None  # 快照目录里的文件为 "snapshot"：资产在 EvalP 里只读
    source_id: str | None = None  # 来源平台上的稳定 ID（规则配置了 id 时）
    display_name: str | None = None  # 来源平台上的原始名称

    def payload(self, with_content: bool) -> dict[str, Any]:
        item: dict[str, Any] = {"path": self.path, "asset": self.asset, "hash": self.hash}
        if self.source_id is not None:
            item["source_id"] = self.source_id
        if self.display_name is not None:
            item["display_name"] = self.display_name
        if self.path.endswith(".csv") and self.asset.startswith("dataset/"):
            item["content_type"] = "text/csv"
        if self.origin:
            item["origin"] = self.origin
            ext = self.path.rsplit(".", 1)[-1].lower() if "." in self.path else ""
            if not self.asset.startswith("file/") and ext in _CONTENT_TYPES:
                item["content_type"] = _CONTENT_TYPES[ext]
        if with_content:
            try:
                item["content"] = self.data.decode("utf-8")
            except UnicodeDecodeError:
                item["content"] = base64.b64encode(self.data).decode("ascii")
                item["encoding"] = "base64"
        return item


@dataclass
class PushResult:
    agent_name: str
    agent_id: str | None
    agent_created: bool
    draft_id: str | None
    revision: int | None
    uploaded: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    unchanged: bool = False
    validation: dict[str, Any] | None = None


@dataclass
class PullResult:
    agent_name: str
    revision: int | None
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------- 工作区


class Workspace:
    """某个 agent 项目目录的同步操作。"""

    def __init__(self, project: ProjectConfig) -> None:
        self.project = project
        self.root = project.root

    # -- 读取

    @property
    def state(self) -> State:
        return State.load(self.project.state_file)

    def load_spec(self) -> dict[str, Any]:
        path = self.project.spec_file
        if not path.is_file():
            raise WorkspaceError(f"找不到 {self.project.spec_path}（[agent].spec）。")
        try:
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise WorkspaceError(f"{self.project.spec_path} 不是合法的 YAML: {e}") from e
        if not isinstance(spec, dict):
            raise WorkspaceError(f"{self.project.spec_path} 的顶层必须是映射（kind、model、prompts……）")
        return spec

    def asset_for_path(self, rel: str) -> str | None:
        """按 ``[assets]`` 映射推出某个本地路径对应的资产（kind/name）。"""
        for pattern, template in self.project.assets.items():
            pat = norm_path(pattern)
            if rel == pat or (_has_glob(pat) and fnmatch.fnmatchcase(rel, pat)):
                return _asset_name(template, Path(rel).stem)
        return None

    def mapped_files(self) -> dict[str, str]:
        """``[assets]`` 展开后实际存在的文件：路径 -> 资产。"""
        out: dict[str, str] = {}
        for pattern in self.project.assets:
            pat = norm_path(pattern)
            if _has_glob(pat):
                found = sorted(p for p in self.root.glob(pat) if p.is_file())
            else:
                found = [self.root / pat] if (self.root / pat).is_file() else []
            for p in found:
                rel = p.relative_to(self.root).as_posix()
                if rel.startswith(f"{STATE_DIR}/"):
                    continue
                asset = self.asset_for_path(rel)
                if asset is not None:
                    out.setdefault(rel, asset)
        return out

    def snapshot_files(self, spec: dict[str, Any]) -> dict[str, str]:
        """代码型 ``snapshot.dir`` 下的全部文件：目录内相对路径 -> 项目相对路径。"""
        snap = spec.get("snapshot")
        snap_dir = snap.get("dir") if isinstance(snap, dict) else snap
        if not snap_dir:
            return {}
        base = self.root / snap_dir
        if not base.is_dir():
            raise WorkspaceError(f"{self.project.spec_path} 的 snapshot.dir 不是目录: {snap_dir}")
        return {
            p.relative_to(base).as_posix(): p.relative_to(self.root).as_posix()
            for p in sorted(base.rglob("*"))
            if p.is_file()
        }

    def snapshot_entries(self, spec: dict[str, Any]) -> dict[str, SnapshotEntry]:
        """快照目录内每个文件对应的资产：目录内相对路径 -> :class:`SnapshotEntry`。

        按 ``snapshot.map`` 的顺序取第一条匹配的规则；没有命中的文件存为 file 资产。
        """
        snap = spec.get("snapshot")
        raw_rules = (snap.get("map") or []) if isinstance(snap, dict) else []
        rules: list[SnapshotRule] = []
        for i, raw in enumerate(raw_rules):
            try:
                rules.append(SnapshotRule.model_validate(raw))
            except ValidationError as e:
                problems = "; ".join(f"{x['path']}: {x['message']}" for x in format_validation_errors(e))
                raise WorkspaceError(
                    f"{self.project.spec_path} 的 snapshot.map[{i}] 不合法: {problems}"
                ) from None
        out: dict[str, SnapshotEntry] = {}
        for rel, path in self.snapshot_files(spec).items():
            rule = next((r for r in rules if glob_match(r.glob, rel)), None)
            out[rel] = (
                snapshot_rule_entry(self.project.agent_name, rule, rel, (self.root / path).read_bytes())
                if rule
                else SnapshotEntry(snapshot_asset(self.project.agent_name, rel))
            )
        return out

    def snapshot_assets(self, spec: dict[str, Any]) -> dict[str, str]:
        """快照目录内每个文件对应的资产：目录内相对路径 -> ``kind/name``。"""
        return {rel: e.asset for rel, e in self.snapshot_entries(spec).items()}

    def collect_files(self, spec: dict[str, Any]) -> list[LocalFile]:
        """要同步的文件：``[assets]`` 映射到的文件，加上 spec 中 ``file:`` 引用的文件和快照目录下的文件。"""
        mapping = self.mapped_files()
        snap_entries: dict[str, SnapshotEntry] = {}
        snap_files = self.snapshot_files(spec)
        for rel, entry in self.snapshot_entries(spec).items():
            mapping[snap_files[rel]] = entry.asset
            snap_entries[snap_files[rel]] = entry
        for ref in iter_file_refs(spec):
            if ref in mapping:
                continue
            asset = self.asset_for_path(ref)
            if asset is None:
                raise WorkspaceError(
                    f"{self.project.spec_path} 引用了 file:{ref}，"
                    f"但 {CONFIG_NAME} 的 [assets] 里没有对应的映射。\n"
                    f'请添加类似 "{ref}" = "prompt/my-prompt" 的一行。'
                )
            if not (self.root / ref).is_file():
                raise WorkspaceError(f"{self.project.spec_path} 引用的文件不存在: {ref}")
            mapping[ref] = asset
        seen: dict[str, str] = {}
        for path, asset in mapping.items():
            if asset in seen:
                raise WorkspaceError(
                    f"文件 {seen[asset]} 与 {path} 映射到同一个资产 {asset}，请调整 [assets] / snapshot.map。"
                )
            seen[asset] = path
        files = []
        for path, asset in sorted(mapping.items()):
            data = (self.root / path).read_bytes()
            entry = snap_entries.get(path)
            files.append(
                LocalFile(
                    path,
                    asset,
                    data,
                    sha256_bytes(data),
                    "snapshot" if entry else None,
                    entry.source_id if entry else None,
                    entry.display_name if entry else None,
                )
            )
        return files

    def _absolutize(self, spec: dict[str, Any]) -> dict[str, Any]:
        """代码型 agent：把相对的 ``source.repo`` 与 ``subprocess.cwd`` 转成绝对路径。"""
        out = copy.deepcopy(spec)
        for section, key in (("source", "repo"), ("subprocess", "cwd")):
            sec = out.get(section)
            if isinstance(sec, dict) and isinstance(sec.get(key), str) and not os.path.isabs(sec[key]):
                sec[key] = str((self.root / sec[key]).resolve())
        return out

    def _fill_snapshot(self, spec: dict[str, Any]) -> dict[str, Any]:
        """把快照目录下的文件登记到 ``snapshot.files``（引用 push 时上传的 file 资产）。"""
        out = copy.deepcopy(spec)
        if out.get("snapshot"):
            snap = out["snapshot"]
            out["snapshot"] = {
                "dir": snap["dir"] if isinstance(snap, dict) else snap,
                **({"map": snap["map"]} if isinstance(snap, dict) and snap.get("map") else {}),
                "files": {
                    rel: f"asset://{asset}@latest" for rel, asset in self.snapshot_assets(spec).items()
                },
            }
        return out

    def _relativize(self, spec: dict[str, Any]) -> dict[str, Any]:
        """pull 时 ``_absolutize`` 的逆操作：绝对路径还原为相对路径；去掉 push 填充的快照文件表。"""
        out = copy.deepcopy(spec)
        if out.get("snapshot"):
            out["snapshot"].pop("files", None)
        for section, key in (("source", "repo"), ("subprocess", "cwd")):
            sec = out.get(section)
            if isinstance(sec, dict) and isinstance(sec.get(key), str) and os.path.isabs(sec[key]):
                try:
                    rel = Path(sec[key]).resolve().relative_to(self.root)
                except ValueError:
                    continue
                sec[key] = rel.as_posix() or "."
        return out

    # -- push

    async def push(self, client: EvalpClient, *, force: bool = False, validate: bool = True) -> PushResult:
        """推送本地 spec 与变化的文件到草稿。冲突时抛 :class:`PushConflict`。"""
        p = self.project
        spec = self.load_spec()
        files = self.collect_files(spec)
        state = self.state
        spec_hash = spec_digest(spec)
        hashes = {f.path: f.hash for f in files}

        # 本地没有任何变化，且服务端草稿 revision 与上次一致：不必再推送
        if (
            not force
            and state.draft_id
            and state.spec_hash == spec_hash
            and state.files == hashes
            and state.draft_revision is not None
        ):
            try:
                draft = await client.get(f"/drafts/{state.draft_id}")
            except EvalpClientError as e:
                if e.status != 404:
                    raise
            else:
                if draft.get("revision") == state.draft_revision:
                    result = PushResult(
                        p.agent_name,
                        state.agent_id,
                        False,
                        state.draft_id,
                        state.draft_revision,
                        skipped=[{"path": f.path, "asset": f.asset, "hash": f.hash} for f in files],
                        unchanged=True,
                    )
                    if validate:
                        result.validation = await self._validate(client, state.draft_id)
                    return result

        body: dict[str, Any] = {
            "agent_name": p.agent_name,
            "draft_name": p.draft_name,
            "spec": self._absolutize(self._fill_snapshot(spec)),
            "expected_revision": None if force else state.draft_revision,
            "force": force,
        }
        if spec.get("kind") in ("config", "code"):
            body["agent_kind"] = spec["kind"]
        changed = {f.path for f in files if state.files.get(f.path) != f.hash}
        body["files"] = [f.payload(f.path in changed) for f in files]
        try:
            out = await self._push_once(client, body)
        except EvalpClientError as e:
            if e.code != "CONTENT_MISSING":
                raise
            # 服务端没有这些 hash 的内容（比如数据目录被重置过）：补上内容后重试一次
            need = {m["path"] for m in e.details.get("missing", [])}
            body["files"] = [f.payload(f.path in changed or f.path in need) for f in files]
            out = await self._push_once(client, body)

        draft = out["draft"]
        new_state = State(
            agent_id=out.get("agent_id"),
            draft_id=draft["id"],
            draft_revision=draft["revision"],
            spec_hash=spec_hash,
            files=hashes,
        )
        new_state.save(p.state_file)
        result = PushResult(
            p.agent_name,
            out.get("agent_id"),
            bool(out.get("agent_created")),
            draft["id"],
            draft["revision"],
            uploaded=out.get("uploaded", []),
            skipped=out.get("skipped", []),
        )
        if validate:
            result.validation = await self._validate(client, draft["id"])
        return result

    async def _push_once(self, client: EvalpClient, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return await client.post("/workspace/push", body)
        except EvalpClientError as e:
            if e.code == "DRAFT_CONFLICT":
                d = e.details
                raise PushConflict(
                    e.message, d.get("current_revision"), d.get("diff"), d.get("server_spec")
                ) from e
            raise

    @staticmethod
    async def _validate(client: EvalpClient, draft_id: str) -> dict[str, Any] | None:
        """校验草稿；校验接口本身失败不影响 push 的结果。"""
        try:
            return await client.post(f"/drafts/{draft_id}/validate", {})
        except EvalpClientError:
            return None

    # -- pull

    async def pull(self, client: EvalpClient, *, force: bool = False) -> PullResult:
        """拉取草稿，把资产内容写回本地文件，``asset://…@latest`` 还原为 ``file:``。

        本地有未推送的修改（与上次同步的哈希不同）时拒绝覆盖，除非 ``force``。
        """
        p = self.project
        state = self.state
        mapping = self.mapped_files()
        for pattern, template in p.assets.items():  # 精确路径的映射：文件被删了也能还原
            if not _has_glob(pattern):
                mapping.setdefault(norm_path(pattern), _asset_name(template, Path(pattern).stem))
        if p.spec_file.is_file():
            try:
                for ref in iter_file_refs(self.load_spec()):
                    asset = self.asset_for_path(ref)
                    if asset is not None:
                        mapping.setdefault(ref, asset)
            except WorkspaceError:
                pass
        asset_map = {asset: path for path, asset in mapping.items()}
        try:
            out = await client.post(
                "/workspace/pull",
                {"agent_name": p.agent_name, "draft_name": p.draft_name, "asset_map": asset_map},
            )
        except EvalpClientError as e:
            if e.status == 404:
                raise WorkspaceError(
                    f"服务端还没有 {p.agent_name}/{p.draft_name}。请先运行 evalp push。"
                ) from e
            raise

        local_spec = self._relativize(out["local_spec"])
        if local_spec.get("snapshot") and isinstance(self.load_spec().get("snapshot"), str):
            local_spec["snapshot"] = local_spec["snapshot"]["dir"]  # 保持本地 agent.yaml 的简写
        contents: dict[str, bytes] = {}
        for a in out["assets"]:
            raw = a["content"]
            contents[a["path"]] = (
                base64.b64decode(raw) if a.get("encoding") == "base64" else raw.encode("utf-8")
            )

        # 覆盖前检查本地是否有未推送的修改
        dirty: list[str] = []
        for path, data in contents.items():
            f = self.root / path
            if f.is_file():
                h = sha256_bytes(f.read_bytes())
                if h != sha256_bytes(data) and h != state.files.get(path):
                    dirty.append(path)
        if p.spec_file.is_file():
            current = self.load_spec()
            if current != local_spec and spec_digest(current) != state.spec_hash:
                dirty.append(p.spec_path)
        if dirty and not force:
            raise WorkspaceError(
                "本地有尚未推送的修改，pull 会覆盖它们:\n  " + "\n  ".join(dirty) + "\n"
                "请先 evalp push，或使用 evalp pull --force 放弃本地修改。"
            )

        result = PullResult(p.agent_name, out["draft"]["revision"], missing=out.get("missing", []))
        for path, data in contents.items():
            f = self.root / path
            if f.is_file() and f.read_bytes() == data:
                result.unchanged.append(path)
            else:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(data)
                result.written.append(path)
        if p.spec_file.is_file() and self.load_spec() == local_spec:
            result.unchanged.append(p.spec_path)
        else:
            p.spec_file.parent.mkdir(parents=True, exist_ok=True)
            old_text = p.spec_file.read_text(encoding="utf-8") if p.spec_file.is_file() else ""
            # 在原文件上更新，保留注释与键顺序（原文件不存在 / 无法往返时退化为整体重写）
            text = dump_preserving(old_text, local_spec) if old_text.strip() else plain_dump(local_spec)
            p.spec_file.write_text(text, encoding="utf-8")
            result.written.append(p.spec_path)
        written_spec = self.load_spec()
        State(
            agent_id=out["agent_id"],
            draft_id=out["draft"]["id"],
            draft_revision=out["draft"]["revision"],
            spec_hash=spec_digest(written_spec),
            files={f.path: f.hash for f in self.collect_files(written_spec)},
        ).save(p.state_file)
        return result
