"""Skill 原始文件包：保留路径与字节，不解包到服务器文件系统。客户端与服务端共用。"""

from __future__ import annotations

import io
import json
import stat
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

PACKAGE_LIMIT = 50 * 1024 * 1024
EXPANDED_LIMIT = 100 * 1024 * 1024
ENTRY_LIMIT = 10_000
PREVIEW_LIMIT = 5 * 1024 * 1024


def legacy_skill_metadata(data: bytes) -> dict[str, Any]:
    """旧导出只有配置时，只提取名称；不伪造文件。"""
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        value = None
    name = (value.get("name") or value.get("Name")) if isinstance(value, dict) else None
    return {"format": "metadata", **({"display_name": name} if isinstance(name, str) and name else {})}


def open_package(data: bytes) -> SkillPackage | None:
    """已入库的 skill 内容是文件包时打开它（内容已在入库时校验）；旧版 JSON 元数据返回 None。"""
    return SkillPackage(data, verify_contents=False) if data.startswith(b"PK") else None


def _check_path(path: str) -> None:
    parts = path.rstrip("/").split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(p in ("", ".", "..") for p in parts)
        or any(ord(c) < 32 for c in path)
        or ":" in parts[0]
        or len(parts) > 128
    ):
        raise ValueError(f"Skill 文件路径不合法: {path!r}")


class SkillPackage:
    def __init__(self, data: bytes, *, verify_contents: bool = True):
        if len(data) > PACKAGE_LIMIT:
            raise ValueError("Skill 压缩包不能超过 50MB")
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
            entries = self.archive.infolist()
            if len(entries) > ENTRY_LIMIT:
                raise ValueError("Skill 文件包不能超过 10000 个条目")
            self.entries: dict[str, zipfile.ZipInfo] = {}
            self.nodes: dict[str, dict[str, Any]] = {}
            total = 0
            for entry in entries:
                _check_path(entry.orig_filename)
                path = entry.filename.rstrip("/")
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                    raise ValueError(f"Skill 不支持符号链接或加密文件: {path}")
                if path in self.entries:
                    raise ValueError(f"Skill 中有重复路径: {path}")
                total += entry.file_size
                if total > EXPANDED_LIMIT:
                    raise ValueError("Skill 解压后的总大小不能超过 100MB")
                self.entries[path] = entry
                node = {
                    "path": path, "type": "directory" if entry.is_dir() else "file", "size": entry.file_size,
                }
                previous = self.nodes.get(path)
                if previous and previous["type"] != node["type"]:
                    raise ValueError(f"Skill 文件与目录路径冲突: {path}")
                self.nodes[path] = node
                for parent in PurePosixPath(path).parents:
                    if str(parent) == ".":
                        continue
                    key = str(parent)
                    if key in self.nodes and self.nodes[key]["type"] != "directory":
                        raise ValueError(f"Skill 文件与目录路径冲突: {key}")
                    self.nodes.setdefault(key, {"path": key, "type": "directory", "size": 0})
            # 验证 CRC 与实际内容，避免损坏压缩包入库。
            if verify_contents:
                for entry in entries:
                    with self.archive.open(entry) as stream:
                        while stream.read(1024 * 1024):
                            pass
            skill_docs = sorted(
                (
                    p for p, e in self.entries.items()
                    if not e.is_dir() and PurePosixPath(p).name == "SKILL.md"
                ),
                key=lambda p: (p.count("/"), p),
            )
            if not skill_docs:
                raise ValueError("Skill 文件包中缺少 SKILL.md")
            self.default_path = skill_docs[0]
            name = PurePosixPath(self.default_path).parent.name or "skill"
            self.frontmatter: dict[str, Any] = {}
            if self.entries[self.default_path].file_size <= PREVIEW_LIMIT:
                try:
                    source = self.read(self.default_path).decode("utf-8-sig")
                    if source.startswith("---\n") or source.startswith("---\r\n"):
                        header = source.split("---", 2)[1]
                        frontmatter = yaml.safe_load(header)
                        if isinstance(frontmatter, dict):
                            self.frontmatter = frontmatter
                        original_name = self.frontmatter.get("name")
                        if isinstance(original_name, str) and original_name:
                            name = original_name
                except (UnicodeDecodeError, yaml.YAMLError):
                    pass
            self.display_name = name
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, EOFError, zlib.error) as exc:
            raise ValueError(f"Skill 压缩包无法读取: {exc}") from None

    def metadata(self) -> dict[str, Any]:
        return {
            "format": "skill_package",
            "display_name": self.display_name,
            "file_count": sum(n["type"] == "file" for n in self.nodes.values()),
            "default_path": self.default_path,
        }

    def tree(self) -> dict[str, Any]:
        return {**self.metadata(), "entries": sorted(self.nodes.values(), key=lambda n: n["path"])}

    def read(self, path: str) -> bytes:
        entry = self.entries.get(path)
        if entry is None or entry.is_dir():
            raise KeyError(path)
        return self.archive.read(entry)

    def text(self, path: str) -> str | None:
        """文本文件内容；二进制或超过预览限制返回 None。"""
        entry = self.entries.get(path)
        if entry is None or entry.is_dir():
            raise KeyError(path)
        if entry.file_size > PREVIEW_LIMIT:
            return None
        try:
            text = self.read(path).decode("utf-8")
        except UnicodeDecodeError:
            return None
        return None if "\x00" in text else text

    def preview(self, path: str) -> dict[str, Any]:
        content = self.text(path)
        return {
            "path": path, "size": self.entries[path].file_size,
            "content": content, "content_available": content is not None,
        }


def package_entries(entries: list[tuple[str, bytes]]) -> bytes:
    """文件夹上传与快照生成确定性 ZIP；显式目录用以 / 结尾的路径表示。"""
    if len(entries) > ENTRY_LIMIT or sum(len(data) for _, data in entries) > EXPANDED_LIMIT:
        raise ValueError("Skill 文件数量或总大小超出限制")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(entries):
            _check_path(path)
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = (stat.S_IFDIR | 0o755) if path.endswith("/") else (stat.S_IFREG | 0o644)
            info.external_attr = mode << 16
            archive.writestr(info, data)
    result = out.getvalue()
    SkillPackage(result)
    return result


def package_directory(root: Path) -> bytes:
    """保留根目录及所有子目录（包括空目录）。符号链接不跟随。"""
    entries: list[tuple[str, bytes]] = []
    total = 0
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_symlink():
            raise ValueError(f"Skill 不支持符号链接: {path}")
        rel = path.relative_to(root.parent).as_posix()
        if path.is_dir():
            entries.append((rel + "/", b""))
        elif path.is_file():
            total += path.stat().st_size
            if total > EXPANDED_LIMIT:
                raise ValueError("Skill 解压后的总大小不能超过 100MB")
            entries.append((rel, path.read_bytes()))
        if len(entries) > ENTRY_LIMIT:
            raise ValueError("Skill 文件包不能超过 10000 个条目")
    return package_entries(entries)
