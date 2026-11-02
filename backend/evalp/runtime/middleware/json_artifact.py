"""从最终回复发现 JSON 链接，并在受限网络范围内读取产物。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from evalp.config import ArtifactsConfig
from evalp.errors import EvalpError
from evalp.runtime.messages import Message
from evalp.runtime.middleware.base import ArtifactResult
from evalp.schemas.middleware import ArtifactCandidate, JsonArtifactConfig


def artifact_error(code: str, message: str) -> EvalpError:
    return EvalpError(message, code=code, status=400)


def pointer(value: Any, path: str) -> Any:
    """按 RFC 6901 选择值；数组索引不能使用负数或前导零。"""
    try:
        for raw in path.split("/")[1:] if path else []:
            key = raw.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise KeyError(key)
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise KeyError(key)
        return value
    except (KeyError, IndexError, ValueError):
        raise artifact_error("ARTIFACT_FIELD_NOT_FOUND", f"JSON 字段不存在：{path}") from None


def parse_json(text: str) -> Any:
    """拒绝非标准 JSON 数值，支持 UTF-8 BOM。"""
    def invalid_constant(value: str) -> None:
        raise ValueError(value)

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("非有限 JSON 数值")
        return parsed
    return json.loads(text.lstrip("\ufeff"), parse_constant=invalid_constant, parse_float=finite_float)


def candidates(source_id: str, final: Message, config: JsonArtifactConfig) -> list[ArtifactCandidate]:
    """仅解析已保存的最终回复，不访问网络；候选 ID 保留签名 URL 身份。"""
    text = final.text()
    documents: list[tuple[str, Any]] = []
    covered: list[tuple[int, int]] = []
    try:
        documents.append(("", parse_json(text)))
        covered.append((0, len(text)))
    except (ValueError, RecursionError):
        pass
    for i, match in enumerate(re.finditer(r"```(?:json)?\s*\n?(.*?)```", text, re.S | re.I)):
        try:
            documents.append((f"code:{i}", parse_json(match[1])))
            covered.append(match.span())
        except (ValueError, RecursionError):
            continue
    found: dict[str, str] = {}

    def add(url: Any, location: str, explicit: bool = False) -> None:
        if not isinstance(url, str):
            return
        try:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                return
            if explicit or parts.path.lower().endswith(".json"):
                found.setdefault(url, location)
        except ValueError:
            return

    def walk(value: Any, location: str, json_field: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                walk(item, f"{location}/{escaped}", json_field or "json" in str(key).lower())
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{location}/{i}", json_field)
        else:
            add(value, location, json_field)

    if config.url_pointer is not None:
        matched = False
        for location, doc in documents:
            try:
                value = pointer(doc, config.url_pointer)
            except EvalpError:
                continue
            matched = True
            add(value, location + config.url_pointer, True)
        if not matched:
            raise artifact_error("ARTIFACT_FIELD_NOT_FOUND", f"回复链接字段不存在：{config.url_pointer}")
    else:
        for location, doc in documents:
            walk(doc, location)
        # Markdown 的 URL 与裸链接分别提取，避免吞入右括号等标点。
        for match in re.finditer(r"\[[^\]]*\]\(\s*(<?https?://[^\s>]+>?)\s*\)", text):
            add(match[1].strip("<>"), "markdown")
            covered.append(match.span())
        for match in re.finditer(r"https?://[^\s<>\"'`)\]}，。；]+", text):
            if not any(start <= match.start() < end for start, end in covered):
                add(match[0], "text")
    return [
        ArtifactCandidate(
            id=hashlib.sha256(f"{source_id}\0{url}".encode()).hexdigest()[:24],
            url=url, location=location,
        )
        for url, location in found.items()
    ]


def select_candidate(items: list[ArtifactCandidate], config: JsonArtifactConfig) -> ArtifactCandidate:
    """单候选自动选择；多候选和无效的历史选择明确失败。"""
    if not items:
        raise artifact_error("ARTIFACT_NOT_FOUND", "上一轮未找到 JSON 产物")
    if config.selected_candidate_id:
        for item in items:
            if item.id == config.selected_candidate_id:
                return item
        raise artifact_error("ARTIFACT_AMBIGUOUS", "产物选择已变化，请重新选择")
    if len(items) != 1:
        raise artifact_error("ARTIFACT_AMBIGUOUS", "上一轮有多个 JSON 产物，请明确选择")
    return items[0]


def validate_url(url: str, limits: ArtifactsConfig) -> None:
    """每次请求和重定向均校验显式配置的域名，禁止 URL 中的凭据。"""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        allowed = {h.strip().lower().rstrip(".") for h in limits.allowed_hosts.split(",") if h.strip()}
        if (
            parsed.scheme not in ("http", "https") or not host or host not in allowed
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or parsed.port not in (None, 80, 443)
        ):
            raise ValueError
    except ValueError:
        raise artifact_error(
            "ARTIFACT_FETCH_FAILED", "产物地址不在允许的读取范围；请配置 artifacts.allowed_hosts"
        ) from None


async def fetch_artifact(url: str, limits: ArtifactsConfig) -> bytes:
    """不携带 Agent 凭据，流式限制解压后的大小，重定向最多三次。"""
    try:
        async with asyncio.timeout(limits.timeout_s):
            async with httpx.AsyncClient(
                timeout=limits.timeout_s, follow_redirects=False, trust_env=False,
                headers={"Accept": "application/json"},
            ) as client:
                current = url
                for _ in range(4):
                    validate_url(current, limits)
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                break
                            current = str(response.url.join(location))
                            # 防止响应 Cookie 被带入后续请求。
                            client.cookies.clear()
                            continue
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16_384):
                            data.extend(chunk)
                            if len(data) > limits.max_download_bytes:
                                raise artifact_error("ARTIFACT_TOO_LARGE", "JSON 下载超过大小限制")
                        return bytes(data)
    except (httpx.HTTPError, TimeoutError):
        # 签名 URL 不进入错误文本；Trace 的来源信息供用户核对。
        raise artifact_error("ARTIFACT_FETCH_FAILED", "JSON 读取失败、链接已过期或读取超时") from None
    raise artifact_error("ARTIFACT_FETCH_FAILED", "JSON 重定向次数超过限制")


def decode_artifact(raw: bytes, url: str, config: JsonArtifactConfig) -> ArtifactResult:
    """解析下载内容并选择指定字段，保留原内容哈希。"""
    try:
        text = raw.decode("utf-8")
        parsed = parse_json(text)
        if not isinstance(parsed, dict | list):
            raise ValueError("scalar")
    except (ValueError, UnicodeError, RecursionError):
        raise artifact_error("ARTIFACT_INVALID_JSON", "产物不是有效的 JSON 对象或数组") from None
    selected = pointer(parsed, config.content_pointer) if config.content_pointer is not None else parsed
    return ArtifactResult(
        url, text, hashlib.sha256(raw).hexdigest(), parsed, selected, config.content_pointer,
    )


def compose_input(original: Message, result: ArtifactResult, limits: ArtifactsConfig) -> Message:
    """完整保留业务指令；产物作为用户数据段附加，不静默截断。"""
    encoded = json.dumps(result.selected, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    text = f"{original.text()}\n\n【上一轮产物】\n来源链接：{result.url}\nJSON 内容：\n{encoded}"
    if len(text.encode("utf-8")) > limits.max_input_bytes:
        raise artifact_error("ARTIFACT_TOO_LARGE", "实际输入超过大小限制，请选择更小的 JSON 字段")
    return original.model_copy(update={"content": text})
