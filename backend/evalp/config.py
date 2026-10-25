"""全局配置（详设 16.2）。

优先级（高到低）：函数参数 overrides > 环境变量 ``EVALP_<SECTION>_<KEY>``
> ``{data_dir}/config.toml`` > 默认值。
数据目录：参数 > ``EVALP_DATA_DIR`` > ``~/.evalp``。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ENV_PREFIX = "EVALP_"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(7860, ge=1, le=65535)


class SchedulerConfig(BaseModel):
    workers: int = Field(32, ge=1)  # 全局并发上限
    default_run_concurrency: int = Field(8, ge=1)
    max_attempts: int = Field(3, ge=1)


class EventsConfig(BaseModel):
    flush_interval_ms: int = Field(50, ge=1)
    flush_batch: int = Field(100, ge=1)
    payload_inline_limit_kb: int = Field(32, ge=1)


class RetentionConfig(BaseModel):
    eval_sessions_days: int = Field(0, ge=0)  # 0 表示永久保留


class Config(BaseModel):
    """平台配置。路径属性均由 data_dir 派生。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    data_dir: Path
    server: ServerConfig = Field(default_factory=ServerConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    events: EventsConfig = Field(default_factory=EventsConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "evalp.db"

    @property
    def blobs_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "token"

    @property
    def secrets_path(self) -> Path:
        return self.data_dir / "secrets.enc"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"

    def ensure_dirs(self) -> None:
        """创建数据目录及 blobs、logs 子目录。"""
        for d in (self.data_dir, self.blobs_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


def resolve_data_dir(data_dir: str | os.PathLike[str] | None = None) -> Path:
    """解析数据目录：参数 > EVALP_DATA_DIR > ~/.evalp。"""
    raw = data_dir or os.environ.get("EVALP_DATA_DIR") or "~/.evalp"
    return Path(raw).expanduser().resolve()


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _env_overrides() -> dict[str, dict[str, str]]:
    """收集 EVALP_<SECTION>_<KEY> 环境变量（字符串，由 pydantic 转换类型）。"""
    out: dict[str, dict[str, str]] = {}
    sections = {name: info.annotation for name, info in Config.model_fields.items() if name != "data_dir"}
    for section, model in sections.items():
        for key in model.model_fields:  # type: ignore[union-attr]
            env = f"{ENV_PREFIX}{section}_{key}".upper()
            if env in os.environ:
                out.setdefault(section, {})[key] = os.environ[env]
    return out


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for section, values in extra.items():
        if isinstance(values, dict):
            base.setdefault(section, {})
            base[section] = {**base[section], **values}
        else:
            base[section] = values
    return base


def load_config(
    data_dir: str | os.PathLike[str] | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> Config:
    """加载配置。overrides 形如 ``{"server": {"port": 7999}}``，优先级最高。"""
    resolved = resolve_data_dir(data_dir)
    merged: dict[str, Any] = {}
    file_cfg = _read_toml(resolved / "config.toml")
    _merge(merged, {k: v for k, v in file_cfg.items() if k in Config.model_fields})
    _merge(merged, _env_overrides())
    if overrides:
        _merge(merged, overrides)
    return Config(data_dir=resolved, **merged)
