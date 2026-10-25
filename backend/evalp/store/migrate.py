"""程序化执行 Alembic 迁移（启动时调用）。"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_alembic_config(db_path: Path | str) -> AlembicConfig:
    """构造指向包内迁移目录的 Alembic 配置（不依赖 alembic.ini）。"""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{Path(db_path).as_posix()}")
    return cfg


def run_migrations(db_path: Path | str, revision: str = "head") -> None:
    """把数据库迁移到指定版本（默认 head）。同步函数，异步环境请用 asyncio.to_thread 调用。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(make_alembic_config(db_path), revision)
