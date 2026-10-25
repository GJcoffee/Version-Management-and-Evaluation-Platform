"""运行时设置：settings 表覆盖 config.toml / 环境变量的默认值。

只接受已知 key（SETTING_SPECS）；值以 JSON 文本存入 settings.value。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from evalp.context import AppContext
from evalp.errors import ValidationFailed
from evalp.store.db import Database
from evalp.store.models import Setting


@dataclass(frozen=True)
class SettingSpec:
    key: str
    min: int
    config_path: tuple[str, str]  # (section, field)，默认值取自 Config


SETTING_SPECS: dict[str, SettingSpec] = {
    s.key: s
    for s in (
        SettingSpec("scheduler.workers", 1, ("scheduler", "workers")),
        SettingSpec("scheduler.default_run_concurrency", 1, ("scheduler", "default_run_concurrency")),
        SettingSpec("scheduler.max_attempts", 1, ("scheduler", "max_attempts")),
        SettingSpec("retention.eval_sessions_days", 0, ("retention", "eval_sessions_days")),
    )
}


class SettingsService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    @property
    def _db(self) -> Database:
        return self.ctx.db

    def _default(self, spec: SettingSpec) -> int:
        section, name = spec.config_path
        return int(getattr(getattr(self.ctx.config, section), name))

    async def get_all(self) -> dict[str, int]:
        """所有已知设置的有效值（settings 表覆盖配置默认值）。"""
        async with self._db.read() as s:
            rows = dict((await s.execute(select(Setting.key, Setting.value))).all())
        out: dict[str, int] = {}
        for key, spec in SETTING_SPECS.items():
            out[key] = json.loads(rows[key]) if key in rows else self._default(spec)
        return out

    async def get(self, key: str) -> int:
        """读取单个设置的有效值。"""
        if key not in SETTING_SPECS:
            raise KeyError(key)
        return (await self.get_all())[key]

    async def update(self, values: dict[str, Any]) -> dict[str, int]:
        """校验并写入设置，返回全部有效值。"""
        errors: list[dict[str, str]] = []
        clean: dict[str, int] = {}
        for key, value in values.items():
            spec = SETTING_SPECS.get(key)
            if spec is None:
                errors.append({"path": f"values.{key}", "message": "未知的设置项", "type": "unknown_key"})
            elif isinstance(value, bool) or not isinstance(value, int):
                errors.append({"path": f"values.{key}", "message": "必须是整数", "type": "int_type"})
            elif value < spec.min:
                errors.append(
                    {"path": f"values.{key}", "message": f"不能小于 {spec.min}", "type": "too_small"}
                )
            else:
                clean[key] = value
        if errors:
            raise ValidationFailed("设置校验失败", {"errors": errors})
        if clean:
            async with self._db.write() as s:
                for key, value in clean.items():
                    row = await s.get(Setting, key)
                    if row is None:
                        s.add(Setting(key=key, value=json.dumps(value)))
                    else:
                        row.value = json.dumps(value)
        return await self.get_all()
