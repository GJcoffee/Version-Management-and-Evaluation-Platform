import pytest
from evalp import __version__
from evalp.ids import new_id
from evalp.store.models import Blob, Job
from evalp.utils.time import iso_ago, now_iso
from sqlalchemy import update

DEFAULTS = {
    "scheduler.workers": 32,
    "scheduler.default_run_concurrency": 8,
    "scheduler.max_attempts": 3,
    "retention.eval_sessions_days": 0,
}


async def test_health_shape(client):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    h = r.json()
    assert h == {
        "ok": True,
        "version": __version__,
        "db_size": h["db_size"],
        "queue": {"pending": 0, "running": 0},
        "workers": {"busy": 0, "total": 32},
        "providers": [],
    }
    assert h["db_size"] > 0


async def test_health_counts_jobs_and_providers(client, ctx):
    now = now_iso()
    async with ctx.db.write() as s:
        for status in ("pending", "pending", "running", "done", "failed"):
            s.add(Job(id=new_id("job"), kind="noop_test", ref_id="x", status=status, run_after=now,
                      created_at=now, updated_at=now))  # fmt: skip
    await client.post("/api/v1/providers", json={"name": "zeta", "base_url": "http://x"})
    await client.post("/api/v1/providers", json={"name": "alpha", "base_url": "http://x"})
    h = (await client.get("/api/v1/health")).json()
    assert h["queue"] == {"pending": 2, "running": 1}
    assert h["providers"] == [
        {"name": "alpha", "inflight": 0, "rpm_left": None, "tpm_left": None},
        {"name": "zeta", "inflight": 0, "rpm_left": None, "tpm_left": None},
    ]


async def test_health_workers_follow_settings(client):
    await client.patch("/api/v1/settings", json={"values": {"scheduler.workers": 5}})
    assert (await client.get("/api/v1/health")).json()["workers"]["total"] == 5


async def test_settings_defaults(client):
    r = await client.get("/api/v1/settings")
    assert r.status_code == 200 and r.json() == {"values": DEFAULTS}


async def test_settings_defaults_follow_config_file(data_dir):
    from evalp.config import load_config

    data_dir.mkdir(parents=True)
    (data_dir / "config.toml").write_text("[scheduler]\nworkers = 4\n")
    from evalp.context import AppContext
    from evalp.services.settings import SettingsService

    c = await AppContext.create(load_config(data_dir))
    try:
        assert (await c.svc(SettingsService).get_all())["scheduler.workers"] == 4
    finally:
        await c.close()


async def test_settings_patch_persists_and_overrides(client, ctx):
    r = await client.patch(
        "/api/v1/settings", json={"values": {"scheduler.workers": 8, "retention.eval_sessions_days": 30}}
    )
    assert r.status_code == 200
    assert r.json()["values"] == {**DEFAULTS, "scheduler.workers": 8, "retention.eval_sessions_days": 30}
    assert (await client.get("/api/v1/settings")).json() == r.json()
    # 再次更新同一个 key（覆盖）
    r = await client.patch("/api/v1/settings", json={"values": {"scheduler.workers": 2}})
    assert r.json()["values"]["scheduler.workers"] == 2
    assert r.json()["values"]["retention.eval_sessions_days"] == 30
    from evalp.services.settings import SettingsService

    assert await ctx.svc(SettingsService).get("scheduler.workers") == 2


@pytest.mark.parametrize(
    "values",
    [
        {"nope": 1},
        {"scheduler.workers": "8"},
        {"scheduler.workers": 1.5},
        {"scheduler.workers": True},
        {"scheduler.workers": 0},
        {"retention.eval_sessions_days": -1},
        {"scheduler.workers": None},
    ],
)
async def test_settings_patch_rejects_bad_input(client, values):
    r = await client.patch("/api/v1/settings", json={"values": values})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await client.get("/api/v1/settings")).json() == {"values": DEFAULTS}  # 未写入


async def test_settings_patch_is_atomic(client):
    r = await client.patch("/api/v1/settings", json={"values": {"scheduler.workers": 3, "bogus": 1}})
    assert r.status_code == 400
    assert (await client.get("/api/v1/settings")).json()["values"]["scheduler.workers"] == 32


async def test_settings_patch_bad_envelope(client):
    assert (await client.patch("/api/v1/settings", json={"scheduler.workers": 3})).status_code == 400
    assert (await client.patch("/api/v1/settings", json={"values": [1]})).status_code == 400


async def test_gc_endpoint(client, ctx):
    old = await ctx.blobs.put_bytes(b"x" * 100)
    keep = await ctx.blobs.put_bytes(b"y" * 10)
    await ctx.blobs.incref(keep)
    async with ctx.db.write() as s:
        await s.execute(update(Blob).values(created_at=iso_ago(hours=30)))
    r = await client.post("/api/v1/system/gc")
    assert r.status_code == 200
    assert r.json() == {"blobs_deleted": 1, "bytes_freed": 100}
    assert not ctx.blobs.exists(old) and ctx.blobs.exists(keep)
    assert (await client.post("/api/v1/system/gc")).json() == {"blobs_deleted": 0, "bytes_freed": 0}
