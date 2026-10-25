"""带类型前缀的 ULID（详设 3.2）。同一进程内严格单调递增。"""

from __future__ import annotations

import os
import threading
import time

from ulid import ULID

PREFIXES: dict[str, str] = {
    "agt": "Agent",
    "drf": "Draft",
    "ver": "Version",
    "ast": "Asset",
    "arv": "AssetRevision",
    "cas": "EvalCase",
    "prv": "Provider",
    "ses": "Session",
    "trn": "Turn",
    "evb": "EvalBatch",
    "evr": "EvalRun",
    "evi": "EvalItem",
    "cmp": "CompareGroup",
    "job": "Job",
}

_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _ulid_str() -> str:
    """生成单调递增的 ULID：同一毫秒内随机部分 +1，保证按生成顺序可排序。"""
    global _last_ms, _last_rand
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            ms = _last_ms
            _last_rand += 1
            if _last_rand >= 1 << 80:  # 溢出：推进一毫秒
                ms += 1
                _last_rand = int.from_bytes(os.urandom(10), "big") >> 1
        else:
            _last_rand = int.from_bytes(os.urandom(10), "big") >> 1  # 留出自增空间
        _last_ms = ms
        raw = ms.to_bytes(6, "big") + _last_rand.to_bytes(10, "big")
    return str(ULID.from_bytes(raw))


def new_id(prefix: str) -> str:
    """生成带前缀的 ID，如 new_id("agt") -> agt_01J…。"""
    if prefix not in PREFIXES:
        raise ValueError(f"未知的 ID 前缀: {prefix!r}，可用: {sorted(PREFIXES)}")
    return f"{prefix}_{_ulid_str()}"


def split_id(value: str) -> tuple[str, str]:
    """拆分 ID，返回 (前缀, ULID)；格式不对时抛 ValueError。"""
    prefix, sep, ulid = value.partition("_")
    if not sep or prefix not in PREFIXES or len(ulid) != 26:
        raise ValueError(f"非法 ID: {value!r}")
    return prefix, ulid


def is_id(value: str, prefix: str | None = None) -> bool:
    """判断字符串是否为合法 ID（可限定前缀）。"""
    try:
        p, _ = split_id(value)
    except ValueError:
        return False
    return prefix is None or p == prefix
