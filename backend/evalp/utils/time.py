"""时间工具：统一使用 UTC ISO8601（毫秒精度，以 Z 结尾）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def now_iso() -> str:
    """返回当前 UTC 时间，如 2026-10-25T08:30:00.123Z。"""
    return to_iso(datetime.now(UTC))


def to_iso(dt: datetime) -> str:
    """把 datetime 转为毫秒精度的 UTC ISO8601 字符串。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """解析 now_iso() 产生的字符串，返回带 UTC 时区的 datetime。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso_ago(*, hours: float = 0, days: float = 0) -> str:
    """返回距今 hours/days 之前的时间（ISO 字符串），便于与库中时间字段比较。"""
    return to_iso(datetime.now(UTC) - timedelta(hours=hours, days=days))


def fmt_local(iso: str | None) -> str:
    """把 UTC ISO 时间转为本机时区的 "YYYY-MM-DD HH:MM"（CLI / MCP 展示用）。"""
    if not iso:
        return ""
    try:
        return parse_iso(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso[:16].replace("T", " ")
