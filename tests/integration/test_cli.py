import runpy
import sys

from evalp import __version__
from evalp.cli import serve as serve_module
from evalp.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def test_help_lists_serve():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0 and "serve" in r.output
    r = runner.invoke(app, ["serve", "--help"])
    assert r.exit_code == 0
    for opt in ("--host", "--port", "--data-dir"):
        assert opt in r.output


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and __version__ in r.output


def test_no_args_shows_help():
    r = runner.invoke(app, [])
    assert "serve" in r.output


def _capture_run(monkeypatch):
    calls = []
    monkeypatch.setattr(
        serve_module.uvicorn, "run", lambda application, **kw: calls.append((application, kw))
    )
    return calls


def test_serve_defaults_loopback_no_warning(tmp_path, monkeypatch):
    calls = _capture_run(monkeypatch)
    r = runner.invoke(app, ["serve", "--data-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert len(calls) == 1
    assert calls[0][1]["host"] == "127.0.0.1" and calls[0][1]["port"] == 7860
    assert "安全警告" not in r.output


def test_serve_non_loopback_warns(tmp_path, monkeypatch):
    calls = _capture_run(monkeypatch)
    r = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--port", "7999", "--data-dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "安全警告" in r.output and "0.0.0.0" in r.output
    assert calls[0][1]["host"] == "0.0.0.0" and calls[0][1]["port"] == 7999


def test_serve_respects_env_data_dir_and_config(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text("[server]\nport = 9123\n")
    monkeypatch.setenv("EVALP_DATA_DIR", str(tmp_path))
    calls = _capture_run(monkeypatch)
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert calls[0][1]["port"] == 9123
    assert calls[0][0].state.config.data_dir == tmp_path.resolve()


def test_python_dash_m_entry(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evalp", "--version"])
    try:
        runpy.run_module("evalp", run_name="__main__")
    except SystemExit as e:
        assert e.code in (0, None)
    assert __version__ in capsys.readouterr().out
