import json
from datetime import UTC, datetime, timedelta

import structlog
from evalp.logging import DailyFileHandler, get_logger, setup_logging, shutdown_logging


def test_json_file_and_context(tmp_path):
    setup_logging(tmp_path, console=False)
    try:
        structlog.contextvars.bind_contextvars(request_id="req1", session_id="ses_x")
        get_logger("evalp.test").info("hello", answer="你好", n=1)
    finally:
        structlog.contextvars.clear_contextvars()
        shutdown_logging()
    day = datetime.now(UTC).strftime("%Y%m%d")
    f = tmp_path / f"evalp-{day}.log"
    assert f.is_file()
    rec = json.loads(f.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["event"] == "hello" and rec["answer"] == "你好" and rec["level"] == "info"
    assert rec["request_id"] == "req1" and rec["session_id"] == "ses_x"
    assert "timestamp" in rec


def test_retention_14_days(tmp_path):
    today = datetime.now(UTC)
    for days in (0, 5, 14, 15, 30):
        d = (today - timedelta(days=days)).strftime("%Y%m%d")
        (tmp_path / f"evalp-{d}.log").write_text("x")
    (tmp_path / "other.log").write_text("keep")
    DailyFileHandler(tmp_path).cleanup()
    names = {p.name for p in tmp_path.iterdir()}
    kept = {f"evalp-{(today - timedelta(days=d)).strftime('%Y%m%d')}.log" for d in (0, 5, 14)}
    assert names == kept | {"other.log"}


def test_setup_is_idempotent(tmp_path):
    import logging

    setup_logging(tmp_path, console=False)
    setup_logging(tmp_path, console=False)
    try:
        mine = [h for h in logging.getLogger("evalp").handlers if isinstance(h, DailyFileHandler)]
        assert len(mine) == 1
    finally:
        shutdown_logging()
    assert not [h for h in logging.getLogger("evalp").handlers if isinstance(h, DailyFileHandler)]
