import sqlite3
import warnings

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from evalp.store.migrate import run_migrations
from evalp.store.models import MANUAL_INDEXES, Base
from sqlalchemy import create_engine

TABLES = {
    "providers", "agents", "drafts", "versions", "version_tags", "version_asset_refs", "blobs", "assets",
    "asset_revisions", "eval_cases", "sessions", "turns", "events", "eval_batches", "eval_runs",
    "eval_items", "eval_scores", "compare_groups", "jobs", "settings",
}  # fmt: skip


@pytest.fixture
def migrated(tmp_path):
    path = tmp_path / "m.db"
    run_migrations(path)
    return path


def test_schema_matches_orm_metadata(migrated):
    """迁移后的实际 schema 与 ORM metadata 无差异（autogenerate compare 为空）。"""
    engine = create_engine(f"sqlite:///{migrated}")
    try:
        with engine.connect() as conn, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mc = MigrationContext.configure(
                conn,
                opts={
                    "compare_type": True,
                    "compare_server_default": True,
                    # 含 DESC 的表达式索引无法反射比较，由 test_indexes_and_without_rowid 单独核对
                    "include_object": lambda o, n, t, r, c: not (t == "index" and n in MANUAL_INDEXES),
                },
            )
            diff = compare_metadata(mc, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_all_tables_present(migrated):
    con = sqlite3.connect(migrated)
    names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    con.close()
    assert TABLES <= names
    assert names - TABLES <= {"alembic_version"}


def test_orm_metadata_covers_design_tables():
    assert set(Base.metadata.tables) == TABLES


def test_idempotent(migrated):
    run_migrations(migrated)  # 再次执行不报错
    con = sqlite3.connect(migrated)
    assert con.execute("select version_num from alembic_version").fetchall() == [("0002",)]
    con.close()


def test_indexes_and_without_rowid(migrated):
    con = sqlite3.connect(migrated)
    idx = {r[0] for r in con.execute("select name from sqlite_master where type='index'")}
    assert {
        "ix_sessions_agent_mode",
        "ix_events_turn",
        "ix_items_cache",
        "ix_items_run_status",
        "ix_jobs_claim",
    } <= idx
    sql = con.execute("select sql from sqlite_master where name='events'").fetchone()[0]
    assert "WITHOUT ROWID" in sql.upper()
    # DESC 排序方向（autogenerate 无法比较的表达式索引，这里直接核对）
    cols = con.execute("pragma index_xinfo('ix_jobs_claim')").fetchall()
    desc = {c[2]: c[3] for c in cols if c[5] == 1}
    assert desc["priority"] == 1 and desc["status"] == 0
    cols = con.execute("pragma index_xinfo('ix_sessions_agent_mode')").fetchall()
    assert {c[2]: c[3] for c in cols if c[5] == 1}["created_at"] == 1
    con.close()


def test_constraints_enforced(migrated):
    con = sqlite3.connect(migrated)
    con.execute("pragma foreign_keys=on")
    with pytest.raises(sqlite3.IntegrityError):  # CHECK(kind)
        con.execute("insert into agents(id,name,kind,created_at,updated_at) values('a','n','bad','t','t')")
    con.execute("insert into agents(id,name,kind,created_at,updated_at) values('a','n','config','t','t')")
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(name)
        con.execute("insert into agents(id,name,kind,created_at,updated_at) values('b','n','code','t','t')")
    with pytest.raises(sqlite3.IntegrityError):  # FK
        con.execute(
            "insert into drafts(id,agent_id,spec,created_at,updated_at) values('d','nope','{}','t','t')"
        )
    con.execute("insert into drafts(id,agent_id,spec,created_at,updated_at) values('d','a','{}','t','t')")
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(agent_id, name)，name 默认 main
        con.execute("insert into drafts(id,agent_id,spec,created_at,updated_at) values('e','a','{}','t','t')")
    row = con.execute("select name, revision from drafts where id='d'").fetchone()
    assert row == ("main", 1)
    # 其他默认值
    con.execute("insert into providers(id,name,base_url,created_at,updated_at) values('p','n','u','t','t')")
    assert con.execute("select headers, models, max_concurrency from providers").fetchone() == ("{}", "[]", 8)
    con.close()


def test_0002_preserves_data_and_adds_columns(tmp_path):
    """0001 -> 0002：已有会话 / 轮次保留，新列有默认值；降级后新列消失。"""
    path = tmp_path / "up.db"
    run_migrations(path, "0001")
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("insert into agents(id,name,kind,created_at,updated_at) values('a1','x','config','t','t')")
    con.execute(
        "insert into versions(id,agent_id,seq,spec_hash,spec,created_via,created_at) "
        "values('v1','a1',1,'h','{}','publish','t')"
    )
    con.execute(
        "insert into sessions(id,agent_id,version_id,mode,created_at,updated_at) "
        "values('s1','a1','v1','chat','t','t')"
    )
    con.execute(
        "insert into turns(id,session_id,idx,status,input,started_at) values('t1','s1',0,'ok','{}','t')"
    )
    con.commit()
    con.close()

    run_migrations(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    assert con.execute("select variables from sessions where id='s1'").fetchone() == ("{}",)
    assert con.execute("select anchored_from_turn_id from turns where id='t1'").fetchone() == (None,)
    # 外键生效：不能指向不存在的轮次；可以指向存在的轮次
    con.execute(
        "insert into turns(id,session_id,idx,status,input,started_at,anchored_from_turn_id) "
        "values('t2','s1',1,'ok','{}','t','t1')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "insert into turns(id,session_id,idx,status,input,started_at,anchored_from_turn_id) "
            "values('t3','s1',2,'ok','{}','t','nope')"
        )
    con.close()


def test_0002_keeps_desc_index_and_downgrade(tmp_path):
    from alembic import command
    from evalp.store.migrate import make_alembic_config

    path = tmp_path / "d.db"
    run_migrations(path)
    con = sqlite3.connect(path)
    sql = con.execute("select sql from sqlite_master where name='ix_sessions_agent_mode'").fetchone()[0]
    assert "created_at DESC" in sql
    con.close()
    command.downgrade(make_alembic_config(path), "0001")
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("pragma table_info(sessions)")}
    tcols = {r[1] for r in con.execute("pragma table_info(turns)")}
    sql = con.execute("select sql from sqlite_master where name='ix_sessions_agent_mode'").fetchone()[0]
    con.close()
    assert "variables" not in cols and "anchored_from_turn_id" not in tcols
    assert "created_at DESC" in sql
