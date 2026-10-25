"""evalp.toolhost 子进程宿主协议。"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def run_host(entry, args=None, *, raw=None, workdir=FIXTURES):
    cmd = [sys.executable, "-m", "evalp.toolhost", "--entry", entry]
    if workdir:
        cmd += ["--workdir", str(workdir)]
    data = raw if raw is not None else json.dumps(args if args is not None else {}, ensure_ascii=False)
    p = subprocess.run(cmd, input=data.encode(), capture_output=True, timeout=60)
    return p


def parse(p):
    lines = p.stdout.decode().splitlines()
    assert len(lines) == 1, p.stdout
    return json.loads(lines[0])


def test_sync_function():
    p = run_host("sample_tools:echo", {"a": 1})
    assert p.returncode == 0
    assert parse(p) == {"ok": True, "result": {"echo": {"a": 1}}}


def test_chinese_roundtrip():
    assert parse(run_host("sample_tools:greet", {"name": "世界"}))["result"] == "你好，世界"


def test_async_function():
    assert parse(run_host("sample_tools:async_double", {"n": 21})) == {"ok": True, "result": 42}


def test_empty_stdin_means_empty_dict():
    assert parse(run_host("sample_tools:echo", raw=""))["result"] == {"echo": {}}


def test_exception_reported_with_traceback():
    out = parse(run_host("sample_tools:boom", {"why": "测试"}))
    assert out["ok"] is False
    err = out["error"]
    assert err["type"] == "ValueError" and "出错了: 测试" in err["message"]
    assert "Traceback" in err["traceback"] and "boom" in err["traceback"]


def test_print_does_not_pollute_stdout():
    p = run_host("sample_tools:noisy")
    assert parse(p) == {"ok": True, "result": "done"}
    err = p.stderr.decode()
    assert "这行 print" in err and "直接写 fd 1" in err and "to stderr" in err


def test_non_serializable_falls_back_to_str():
    out = parse(run_host("sample_tools:not_serializable"))
    assert out["ok"] is True and isinstance(out["result"]["obj"], str)


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        ("sample_tools:missing", "AttributeError"),
        ("no_such_mod:f", "ModuleNotFoundError"),
        ("bad-entry", "ValueError"),
    ],
)
def test_bad_entry(entry, kind):
    out = parse(run_host(entry))
    assert out["ok"] is False and out["error"]["type"] == kind


def test_bad_json_input():
    out = parse(run_host("sample_tools:echo", raw="{not json"))
    assert out["ok"] is False and out["error"]["type"] == "JSONDecodeError"


def test_workdir_on_sys_path_without_cwd_change():
    out = parse(run_host("sample_tools:cwd_and_path"))
    assert out["result"]["has_workdir"] is True
