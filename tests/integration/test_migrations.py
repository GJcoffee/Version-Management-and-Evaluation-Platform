import json
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
    "adapter_credentials", "adapter_environments", "adapter_environment_revisions",
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
    assert con.execute("select version_num from alembic_version").fetchall() == [("0009",)]
    con.close()


def test_0006_freezes_legacy_session_binding_without_changing_spec(tmp_path):
    path = tmp_path / "legacy.db"
    run_migrations(path, "0005")
    original = {"kind": "code", "transport": "http", "http": {"base_url": "http://old"}}
    with sqlite3.connect(path) as conn:
        conn.execute(
            "insert into agents(id,name,kind,created_at,updated_at) values('a','old','code','t','t')"
        )
        conn.execute(
            "insert into versions(id,agent_id,seq,spec_hash,spec,created_via,created_at)"
            " values('v','a',1,'original',?,'api','t')", (json.dumps(original),),
        )
        conn.execute(
            "insert into sessions(id,agent_id,version_id,mode,created_at,updated_at)"
            " values('s','a','v','chat','t','t')",
        )
    run_migrations(path)
    with sqlite3.connect(path) as conn:
        snapshot = json.loads(conn.execute("select adapter_snapshot from sessions").fetchone()[0])
        assert snapshot == {"revision": 0, "binding": None, "source": "legacy"}
        row = conn.execute("select spec_hash,spec from versions").fetchone()
        assert row[0] == "original" and json.loads(row[1]) == original


def _seed_versions(conn, specs):
    """specs: [(agent, seq, created_at, spec)]；同一 agent 只建一次。"""
    for agent in {a for a, *_ in specs}:
        conn.execute(
            "insert into agents(id,name,kind,created_at,updated_at) values(?,?,'code','t','t')",
            (agent, agent),
        )
    for agent, seq, created, spec in specs:
        conn.execute(
            "insert into versions(id,agent_id,seq,spec_hash,spec,created_via,created_at)"
            " values(?,?,?,?,?,'api',?)",
            (f"{agent}-v{seq}", agent, seq, f"h-{agent}-{seq}", json.dumps(spec), created),
        )


def _revision(conn, version, number, host, created, reason, binding=True):
    value = {"id": "hiagent", "version": "1.0.0", "credential_ref": "adapter:x",
             "config": {"base_url": f"https://{host}", "app_id": "a", "user_id": "u",
                        "published_version": "1"}}
    snapshot = {"revision": number, "binding": value if binding else None, "source": "override",
                "fingerprint": f"fp-{host}"}
    conn.execute(
        "insert into adapter_revisions(version_id,revision,snapshot,reason,created_at) values(?,?,?,?,?)",
        (version, number, json.dumps(snapshot), reason, created),
    )
    return value


def test_0008_revisions_are_numbered_oldest_first_and_the_latest_is_current(tmp_path):
    """多个版本各有修订：按 created_at 从旧到新编为 #1…，最新的一条 = 最大号 = 当前生产；测试为空。"""
    path = tmp_path / "envs.db"
    run_migrations(path, "0007")
    plain = {"kind": "code", "transport": "adapter"}
    with sqlite3.connect(path) as conn:
        _seed_versions(conn, [("a", 1, "t1", plain), ("a", 2, "t2", plain), ("b", 1, "t3", plain)])
        _revision(conn, "a-v1", 1, "old-a1", "2026-01-01T00:00:00Z", "第一次")
        newest = _revision(conn, "b-v1", 1, "newest", "2026-03-01T00:00:00Z", "最新")
        _revision(conn, "a-v1", 2, "mid-a1", "2026-02-01T00:00:00Z", "第二次")
        _revision(conn, "a-v2", 1, "old-a2", "2025-12-01T00:00:00Z", "更早")
        _revision(conn, "a-v2", 2, "cleared", "2026-03-02T00:00:00Z", "撤销覆盖", binding=False)
        conn.execute(
            "insert into sessions(id,agent_id,version_id,mode,created_at,updated_at,adapter_snapshot)"
            " values('s','a','a-v1','chat','t','t','{\"revision\":1}')"
        )
    run_migrations(path)
    with sqlite3.connect(path) as conn:
        envs = dict(conn.execute("select name, revision from adapter_environments"))
        assert envs == {"production": 4, "test": 0}  # 当前 = 最大号
        rows = conn.execute(
            "select revision, binding, source, reason, created_at from adapter_environment_revisions"
            " where environment='production' order by revision"
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3, 4]
        assert {r[2] for r in rows} == {"migration"}
        assert [r[4] for r in rows] == [
            "2025-12-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z"]
        # 最新那条是最大号，也就是当前生效的存档；说明写明来自哪个版本的哪个修订。
        assert json.loads(rows[3][1]) == newest
        assert "b:v1" in rows[3][3] and "修订 #1" in rows[3][3] and "最新" in rows[3][3]
        assert "a:v2" in rows[0][3] and "更早" in rows[0][3]
        assert "a:v1" in rows[2][3] and "修订 #2" in rows[2][3]
        assert conn.execute(
            "select updated_at from adapter_environments where name='production'").fetchone() == (rows[3][4],)
        assert conn.execute("select count(*) from adapter_environment_revisions where environment='test'"
                            ).fetchone() == (0,)
        assert conn.execute("select adapter_snapshot from sessions").fetchone() == ('{"revision":1}',)
        assert not conn.execute("select 1 from sqlite_master where name='adapter_revisions'").fetchone()


def test_0008_without_revisions_uses_the_latest_spec_adapter(tmp_path):
    path = tmp_path / "spec.db"
    run_migrations(path, "0007")
    binding = {"id": "hiagent", "version": "1.0.0", "credential_ref": "adapter:s", "config": {
        "base_url": "https://spec", "app_id": "a", "user_id": "u", "published_version": "1"}}
    older = {**binding, "config": {**binding["config"], "base_url": "https://older"}}
    with sqlite3.connect(path) as conn:
        _seed_versions(conn, [
            ("a", 1, "2026-01-01T00:00:00Z", {"kind": "code", "transport": "adapter", "adapter": older}),
            ("a", 2, "2026-02-01T00:00:00Z", {"kind": "code", "transport": "adapter", "adapter": binding}),
            ("a", 3, "2026-03-01T00:00:00Z", {"kind": "code", "transport": "http"}),
        ])
    run_migrations(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "select revision from adapter_environments where name='production'").fetchone() == (1,)
        rows = conn.execute(
            "select revision, binding, source, reason from adapter_environment_revisions").fetchall()
        assert len(rows) == 1 and rows[0][0] == 1 and json.loads(rows[0][1]) == binding
        assert rows[0][2] == "migration" and "a:v2" in rows[0][3] and "spec.adapter" in rows[0][3]


def test_0008_with_nothing_to_migrate_leaves_both_environments_empty(tmp_path):
    path = tmp_path / "empty.db"
    run_migrations(path)
    with sqlite3.connect(path) as conn:
        assert dict(conn.execute("select name, revision from adapter_environments")) == {
            "production": 0, "test": 0}
        assert conn.execute("select count(*) from adapter_environment_revisions").fetchone() == (0,)


def test_0008_downgrade_restores_version_revisions_best_effort(tmp_path):
    from alembic import command
    from evalp.store.migrate import make_alembic_config

    path = tmp_path / "down.db"
    run_migrations(path, "0007")
    plain = {"kind": "code", "transport": "adapter"}
    with sqlite3.connect(path) as conn:
        _seed_versions(conn, [("a", 1, "t1", plain), ("b", 1, "t2", plain)])
        first = _revision(conn, "a-v1", 1, "one", "2026-01-01T00:00:00Z", "第一次")
        second = _revision(conn, "b-v1", 1, "two", "2026-02-01T00:00:00Z", "第二次")
    run_migrations(path)
    with sqlite3.connect(path) as conn:  # 升级后又在新模型里追加了两条，降级时无法归属到版本
        for env, number in (("production", 3), ("test", 1)):
            conn.execute(
                "insert into adapter_environment_revisions values(?,?,'{}','edit','新',NULL,'t')",
                (env, number),
            )
    command.downgrade(make_alembic_config(path), "0007")
    with sqlite3.connect(path) as conn:
        rows = {r[0]: r for r in conn.execute(
            "select version_id, revision, snapshot, reason, created_at from adapter_revisions")}
        assert set(rows) == {"a-v1", "b-v1"}
        assert json.loads(rows["a-v1"][2])["binding"] == first and rows["a-v1"][3] == "第一次"
        assert json.loads(rows["b-v1"][2])["binding"] == second and rows["b-v1"][4] == "2026-02-01T00:00:00Z"
        assert json.loads(rows["b-v1"][2])["fingerprint"]
        names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        assert "adapter_environments" not in names and "adapter_environment_revisions" not in names
    run_migrations(path)  # 降级后可再次升级


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
    assert con.execute(
        "select effective_input,middleware_snapshot from turns where id='t1'"
    ).fetchone() == (None, None)
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


def test_0009_adds_milestone_columns_and_downgrades(tmp_path):
    """0008 -> 0009：已有版本成为未归档的普通版本；archived_into 外键有效；降级后三列消失、版本保留。"""
    from alembic import command
    from evalp.store.migrate import make_alembic_config

    path = tmp_path / "ms.db"
    run_migrations(path, "0008")
    plain = {"kind": "code", "transport": "adapter"}
    with sqlite3.connect(path) as conn:
        _seed_versions(conn, [("a", 1, "t1", plain), ("a", 2, "t2", plain)])

    run_migrations(path)
    with sqlite3.connect(path) as conn:
        conn.execute("pragma foreign_keys=on")
        rows = conn.execute("select id, milestone, milestone_title, archived_into from versions order by seq")
        assert rows.fetchall() == [("a-v1", 0, None, None), ("a-v2", 0, None, None)]
        conn.execute("update versions set milestone=1, milestone_title='第一阶段' where id='a-v2'")
        conn.execute("update versions set archived_into='a-v2' where id='a-v1'")
        with pytest.raises(sqlite3.IntegrityError):  # 外键：只能归档到存在的版本
            conn.execute("update versions set archived_into='nope' where id='a-v1'")
        # 迁移重建 versions 表后，其他表对它的外键仍然有效
        conn.execute("insert into sessions(id,agent_id,version_id,mode,created_at,updated_at)"
                     " values('s','a','a-v1','chat','t','t')")  # fmt: skip

    command.downgrade(make_alembic_config(path), "0008")
    with sqlite3.connect(path) as conn:
        cols = {r[1] for r in conn.execute("pragma table_info(versions)")}
        assert not ({"milestone", "milestone_title", "archived_into"} & cols)
        assert conn.execute("select id from versions order by seq").fetchall() == [("a-v1",), ("a-v2",)]
        assert conn.execute("select version_id from sessions").fetchall() == [("a-v1",)]
