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
    assert con.execute("select version_num from alembic_version").fetchall() == [("0005",)]
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


def _seed_replay_groups(path):
    """0002 库：一个已完成的 replay 组、一个未跑完的、一个带自由备注的 history 组。"""
    con = sqlite3.connect(path)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("insert into agents(id,name,kind,created_at,updated_at) values('a1','x','config','t','t')")
    con.execute(
        "insert into versions(id,agent_id,seq,spec_hash,spec,created_via,created_at) "
        "values('v1','a1',1,'h','{}','publish','t')"
    )
    for sid, mode in (("src", "chat"), ("done", "replay"), ("half", "replay")):
        con.execute(
            "insert into sessions(id,agent_id,version_id,mode,created_at,updated_at) "
            f"values('{sid}','a1','v1','{mode}','t','t')"
        )
    for sid, pairs in (("done", (1, 3)), ("half", (1,))):
        for i, p in enumerate(pairs):
            con.execute(
                "insert into turns(id,session_id,idx,pair_index,status,input,started_at) "
                f"values('{sid}{i}','{sid}',{i},{p},'ok','{{}}','t')"
            )
    rows = [
        ("c1", "replay", "src", "done", '{"replay_turns": [1, 3]}'),
        ("c2", "replay", "src", "half", '{"replay_turns": [1, 2]}'),
        ("c3", "history", "src", "done", "我的备注"),
    ]
    for cid, mode, left, right, notes in rows:
        con.execute(
            "insert into compare_groups(id,kind,mode,left_ref,right_ref,notes,created_at) "
            "values(?, 'session', ?, ?, ?, ?, 't')",
            (cid, mode, left, right, notes),
        )
    con.commit()
    con.close()


def test_0003_moves_replay_state_out_of_notes(tmp_path):
    """0002 -> 0003：notes 里的所选轮次迁到 replay_turns，状态按已有轮次推断，用户备注原样保留。"""
    path = tmp_path / "r.db"
    run_migrations(path, "0002")
    _seed_replay_groups(path)
    run_migrations(path)
    con = sqlite3.connect(path)
    got = {
        r[0]: r[1:]
        for r in con.execute("select id, replay_status, replay_turns, notes from compare_groups order by id")
    }
    assert got["c1"] == ("completed", "[1, 3]", None)
    assert got["c2"][0] == "failed" and got["c2"][1] == "[1, 2]" and got["c2"][2] is None
    assert got["c3"] == (None, None, "我的备注")
    err = con.execute("select replay_error from compare_groups where id='c2'").fetchone()[0]
    assert err
    with pytest.raises(sqlite3.IntegrityError):  # CHECK(replay_status)
        con.execute("update compare_groups set replay_status='bogus' where id='c1'")
    con.close()


def test_0003_downgrade_restores_notes(tmp_path):
    from alembic import command
    from evalp.store.migrate import make_alembic_config

    path = tmp_path / "rd.db"
    run_migrations(path, "0002")
    _seed_replay_groups(path)
    run_migrations(path)
    command.downgrade(make_alembic_config(path), "0002")
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("pragma table_info(compare_groups)")}
    notes = dict(con.execute("select id, notes from compare_groups"))
    con.close()
    assert not ({"replay_status", "replay_turns", "replay_error"} & cols)
    assert notes["c1"] == '{"replay_turns": [1, 3]}' and notes["c3"] == "我的备注"


def test_0004_allows_snapshot_asset_kinds_and_downgrades(tmp_path):
    """0003 -> 0004：已有资产保留，kind 约束放宽到快照入库的新类型；降级时新类型改回 file。"""
    from alembic import command
    from evalp.store.migrate import make_alembic_config

    path = tmp_path / "k.db"
    run_migrations(path, "0003")
    con = sqlite3.connect(path)
    con.execute("insert into assets(id,kind,name,created_at) values('a1','prompt','p','t')")
    with pytest.raises(sqlite3.IntegrityError):  # 旧约束不认识 skill
        con.execute("insert into assets(id,kind,name,created_at) values('a2','skill','s','t')")
    con.commit()
    con.close()

    run_migrations(path)
    con = sqlite3.connect(path)
    for i, kind in enumerate(("kb_config", "skill", "mcp", "database", "app_config")):
        con.execute("insert into assets(id,kind,name,created_at) values(?,?,?,'t')", (f"n{i}", kind, f"x{i}"))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("insert into assets(id,kind,name,created_at) values('bad','nope','x','t')")
    with pytest.raises(sqlite3.IntegrityError):  # (kind, name) 唯一约束仍在
        con.execute("insert into assets(id,kind,name,created_at) values('dup','skill','x1','t')")
    con.commit()
    con.close()

    command.downgrade(make_alembic_config(path), "0003")
    con = sqlite3.connect(path)
    assert con.execute("select kind from assets where id like 'n%'").fetchall() == [("file",)] * 5
    assert con.execute("select kind from assets where id = 'a1'").fetchone() == ("prompt",)
    con.close()


def test_0005_adds_conventions_and_asset_source_columns(tmp_path):
    """0004 -> 0005：已有数据保留并取默认值；(kind, source_id) 唯一且允许多个 NULL；降级后列消失。"""
    from alembic import command
    from evalp.store.migrate import make_alembic_config

    path = tmp_path / "s.db"
    run_migrations(path, "0004")
    con = sqlite3.connect(path)
    con.execute("insert into agents(id,name,kind,created_at,updated_at) values('a1','x','code','t','t')")
    con.execute("insert into assets(id,kind,name,created_at) values('s1','skill','crawl','t')")
    con.commit()
    con.close()

    run_migrations(path)
    con = sqlite3.connect(path)
    assert con.execute("select conventions from agents where id='a1'").fetchone() == ("{}",)
    assert con.execute("select display_name, source_id, meta from assets where id='s1'").fetchone() == (
        None,
        None,
        "{}",
    )
    con.execute(
        "insert into assets(id,kind,name,created_at) values('s2','skill','other','t')"
    )  # 多个 NULL 不冲突
    con.execute("update assets set source_id='k1' where id='s1'")
    con.execute("insert into assets(id,kind,name,source_id,created_at) values('m1','mcp','a','k1','t')")
    with pytest.raises(sqlite3.IntegrityError):  # 同类型下 source_id 唯一
        con.execute("insert into assets(id,kind,name,source_id,created_at) values('s3','skill','b','k1','t')")
    con.commit()
    con.close()

    command.downgrade(make_alembic_config(path), "0004")
    con = sqlite3.connect(path)
    assert "conventions" not in {r[1] for r in con.execute("pragma table_info(agents)")}
    assert not (
        {"display_name", "source_id", "meta"} & {r[1] for r in con.execute("pragma table_info(assets)")}
    )
    con.close()
