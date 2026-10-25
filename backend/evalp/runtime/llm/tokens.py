"""token 估算（tiktoken ``cl100k_base``）。

仅用于限流预扣和服务端不返回 usage 时的兜底，结果是估算值。tiktoken 的词表需要联网下载一次；
离线且无缓存时退化为启发式估算（CJK 字符约 1 token / 字，其他约 4 字符 / token）。
"""

from __future__ import annotations

import json
import threading
from typing import Any

_lock = threading.Lock()
_encoder: Any = None
_encoder_failed = False

# 每条消息的固定开销、回复起始开销（OpenAI 官方估算方法）
_PER_MESSAGE = 4
_REPLY_PRIMING = 3


def _get_encoder() -> Any | None:
    global _encoder, _encoder_failed
    if _encoder is not None or _encoder_failed:
        return _encoder
    with _lock:
        if _encoder is None and not _encoder_failed:
            try:
                import tiktoken

                _encoder = tiktoken.get_encoding("cl100k_base")
            except Exception:  # 离线 / 词表不可用
                _encoder_failed = True
    return _encoder


def _heuristic(text: str) -> int:
    cjk = sum(1 for ch in text if "⺀" <= ch <= "鿿" or "가" <= ch <= "힯")
    return cjk + (len(text) - cjk + 3) // 4


def count_tokens(text: str) -> int:
    """估算一段文本的 token 数。"""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is None:
        return _heuristic(text)
    return len(enc.encode(text, disallowed_special=()))


def _message_text(msg: Any) -> str:
    """取出消息（Message 或 dict）参与计数的全部文本。"""
    d = msg if isinstance(msg, dict) else msg.model_dump(mode="json", exclude_none=True)
    parts: list[str] = []
    content = d.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        parts.extend(str(p.get("text", "")) for p in content if isinstance(p, dict))
    if d.get("name"):
        parts.append(str(d["name"]))
    for tc in d.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) and "function" in tc else tc
        if not isinstance(fn, dict):
            continue
        parts.append(str(fn.get("name", "")))
        args = fn.get("arguments", "")
        parts.append(args if isinstance(args, str) else json.dumps(args, ensure_ascii=False))
    return "\n".join(parts)


def count_messages_tokens(messages: list[Any], tools: list[dict[str, Any]] | None = None) -> int:
    """估算一次请求的 prompt token 数（消息 + 工具定义）。"""
    total = _REPLY_PRIMING
    for m in messages:
        total += _PER_MESSAGE + count_tokens(_message_text(m))
    if tools:
        total += count_tokens(json.dumps(tools, ensure_ascii=False))
    return total
