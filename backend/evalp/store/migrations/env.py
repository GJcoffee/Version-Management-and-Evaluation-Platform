"""Alembic 环境：同步 SQLite 引擎，目标元数据为 evalp.store.models.Base.metadata。"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from evalp.store.models import MANUAL_INDEXES, Base

config = context.config
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201
    """忽略无法反射比较的手工索引。"""
    return not (type_ == "index" and name in MANUAL_INDEXES)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,  # SQLite 修改表结构需要 batch 模式
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                render_as_batch=True,
                include_object=include_object,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
