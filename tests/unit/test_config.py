from pathlib import Path

from evalp.config import load_config, resolve_data_dir


def test_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 7860
    assert cfg.scheduler.workers == 32
    assert cfg.scheduler.default_run_concurrency == 8
    assert cfg.scheduler.max_attempts == 3
    assert cfg.events.flush_interval_ms == 50
    assert cfg.events.flush_batch == 100
    assert cfg.events.payload_inline_limit_kb == 32
    assert cfg.retention.eval_sessions_days == 0
    assert cfg.db_path == tmp_path.resolve() / "evalp.db"
    assert cfg.token_path.name == "token"


def test_data_dir_precedence(tmp_path, monkeypatch):
    assert resolve_data_dir() == (Path.home() / ".evalp").resolve()
    monkeypatch.setenv("EVALP_DATA_DIR", str(tmp_path / "env"))
    assert resolve_data_dir() == (tmp_path / "env").resolve()
    assert resolve_data_dir(tmp_path / "arg") == (tmp_path / "arg").resolve()


def test_config_toml(tmp_path):
    (tmp_path / "config.toml").write_text(
        "[server]\nport = 9000\n[scheduler]\nworkers = 4\n", encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.server.port == 9000
    assert cfg.server.host == "127.0.0.1"  # 未写的字段保持默认
    assert cfg.scheduler.workers == 4


def test_env_overrides_toml_and_args_override_env(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("[server]\nport = 9000\n", encoding="utf-8")
    monkeypatch.setenv("EVALP_SERVER_PORT", "9100")
    monkeypatch.setenv("EVALP_SCHEDULER_MAX_ATTEMPTS", "5")
    cfg = load_config(tmp_path)
    assert cfg.server.port == 9100
    assert cfg.scheduler.max_attempts == 5
    cfg = load_config(tmp_path, {"server": {"port": 9200}})
    assert cfg.server.port == 9200


def test_ensure_dirs(tmp_path):
    cfg = load_config(tmp_path / "a" / "b")
    cfg.ensure_dirs()
    assert cfg.blobs_dir.is_dir() and cfg.logs_dir.is_dir()
