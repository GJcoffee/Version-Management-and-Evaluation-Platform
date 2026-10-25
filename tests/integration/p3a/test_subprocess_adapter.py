"""SubprocessAdapter：L1 / L2、stderr 与噪声转 log、退出码、环境变量与 cwd、超时 / 取消时杀掉进程树。"""

import asyncio
import json
import sys
import textwrap
import time

import psutil
import pytest
from evalp.runtime.adapters.subprocess_adapter import SubprocessAdapter
from evalp.spec.models import parse_spec

from .conftest import SDK_AGENT


def py(code: str) -> list[str]:
    """用当前解释器执行一段代码的命令行。"""
    return [sys.executable, "-c", textwrap.dedent(code)]


async def sub_version(world, command, **kw):
    spec = {"transport": "subprocess", "subprocess": {"command": command, **kw.pop("subprocess", {})}, **kw}
    return await world.code_version(spec)


def types(events):
    return [e.type for e in events]


def alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def wait_file(path, timeout=10):
    for _ in range(int(timeout / 0.05)):
        if path.exists() and path.read_text().strip():
            return path.read_text()
        await asyncio.sleep(0.05)
    raise AssertionError(f"{path} 没有出现")


# ---------------------------------------------------------------- L2：evalp-sdk 示例 agent


async def test_sdk_stdio_agent_full_trace(world):
    v = await world.subprocess_version()
    s, t = await world.chat(v, "你好")
    assert t.status == "ok" and t.output.final.content == "echo[0]: 你好"
    assert t.usage == {"prompt_tokens": 11, "completion_tokens": 7, "llm_calls": 1, "tool_calls": 1}
    events = await world.events(s.id)
    # print 被 SDK 重定向到 stderr，平台把 stderr 记为 warn 级别的 log 事件
    logs = [e for e in events if e.type == "log" and e.payload.get("source") == "stderr"]
    assert logs and logs[0].payload["level"] == "warn" and "print" in logs[0].payload["message"]
    assert [e.type for e in events if e.type != "log"] == [
        "turn.start", "span.start", "span.end", "llm.request", "llm.response",
        "tool.call", "tool.result", "message.assistant", "turn.end",
    ]  # fmt: skip
    t2 = await world.sessions.run_turn(s.id, "第二问")
    assert t2.output.final.content == "echo[2]: 第二问"


async def test_env_injection_and_spec_env(world, monkeypatch):
    monkeypatch.setenv("FROM_PLATFORM", "平台环境")
    monkeypatch.setenv("SECRET_FROM_ENV", "s3cret")
    v = await world.subprocess_version(env={"SPEC_VAR": "x-${env:SECRET_FROM_ENV}"})
    s = await world.session(v)
    t = await world.sessions.run_turn(s.id, "env:EVALP_SESSION_ID")
    assert t.output.final.content == s.id
    t = await world.sessions.run_turn(s.id, "env:EVALP_TURN_ID")
    assert t.output.final.content == t.id
    t = await world.sessions.run_turn(s.id, "env:FROM_PLATFORM")
    assert t.output.final.content == "平台环境"
    t = await world.sessions.run_turn(s.id, "env:SPEC_VAR")
    assert t.output.final.content == "x-s3cret"


async def test_relative_cwd_resolved_against_source_repo(world, tmp_path):
    repo = tmp_path / "myrepo"
    (repo / "agent").mkdir(parents=True)
    v = await world.subprocess_version(
        subprocess={"command": [sys.executable, str(SDK_AGENT), "stdio"], "cwd": "agent"},
        source={"repo": str(repo), "capture_git": False},
    )
    _, t = await world.chat(v, "cwd")
    assert t.status == "ok" and t.output.final.content == str((repo / "agent").resolve())


async def test_relative_repo_and_default_cwd_use_process_cwd(world, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "proj").mkdir()
    v = await world.subprocess_version(source={"repo": "proj", "capture_git": False})
    _, t = await world.chat(v, "cwd")
    assert t.output.final.content == str((tmp_path / "proj").resolve())


async def test_variables_tool_mocks_and_history_sent_via_stdin(world):
    code = """
        import json, sys
        req = json.load(sys.stdin)
        print(json.dumps({"output": {"role": "assistant", "content": json.dumps(req, ensure_ascii=False)}}))
    """
    v = await sub_version(world, py(code), params={"k": 1})
    s = await world.session(v, variables={"a": 1})
    await world.sessions.run_turn(s.id, "一")
    t = await world.sessions.run_turn(s.id, "二", variables={"b": 2}, tool_mocks={"t": []})
    req = json.loads(t.output.final.content)
    assert req["protocol"] == "evalp-agent/1" and req["session_id"] == s.id and req["turn_id"] == t.id
    assert (
        req["variables"] == {"a": 1, "b": 2} and req["tool_mocks"] == {"t": []} and req["params"] == {"k": 1}
    )
    assert [m["role"] for m in req["history"]] == ["user", "assistant"] and req["input"]["content"] == "二"


# ---------------------------------------------------------------- L1 / 噪声 / 退出码


async def test_l1_single_line(world):
    code = """
        import json
        out = {"role": "assistant", "content": "一行 L1"}
        print(json.dumps({"output": out, "usage": {"prompt_tokens": 3, "completion_tokens": 2}}))
    """
    v = await sub_version(world, py(code))
    s, t = await world.chat(v, "x")
    assert t.status == "ok" and t.output.final.content == "一行 L1" and t.usage["prompt_tokens"] == 3
    assert types(await world.events(s.id)) == ["turn.start", "message.assistant", "turn.end"]


async def test_l1_pretty_printed_multiline(world):
    code = """
        import json
        print(json.dumps({"output": {"role": "assistant", "content": "多行 L1"}}, indent=2))
    """
    v = await sub_version(world, py(code))
    _, t = await world.chat(v, "x")
    assert t.status == "ok" and t.output.final.content == "多行 L1"


async def test_stdout_noise_becomes_log_events(world):
    code = """
        import json
        print("调试输出一")
        msg = {"role": "assistant", "content": "好"}
        print(json.dumps({"type": "message.assistant", "payload": {"message": msg}}))
        print("调试输出二")
        print(json.dumps({"unrelated": True}))
    """
    v = await sub_version(world, py(code))
    s, t = await world.chat(v, "x")
    assert t.status == "ok"
    logs = [e.payload["message"] for e in await world.events(s.id) if e.type == "log"]
    assert (
        "[stdout] 调试输出一" in logs
        and "[stdout] 调试输出二" in logs
        and any("unrelated" in m for m in logs)
    )


@pytest.mark.parametrize(
    "code,expect",
    [
        ('print("只是文本")', "既不是事件流"),
        ("pass", "没有向 stdout 输出"),
        ('import json; print(json.dumps({"type": "bogus", "payload": {}}))', "未知的事件类型"),
        (
            'import json; print(json.dumps({"type": "tool.call", "payload": {}}))',
            "tool.call 的 payload 不合法",
        ),
        ('import json; print(json.dumps({"output": {"role": "user", "content": "x"}}))', "assistant"),
    ],
)
async def test_protocol_violations_are_readable(world, code, expect):
    v = await sub_version(world, py(code))
    s, t = await world.chat(v, "x")
    assert t.status == "error" and t.error.code == "AGENT_PROTOCOL_ERROR" and expect in t.error.message
    assert types(await world.events(s.id))[-1] == "turn.end"


async def test_nonzero_exit_without_output(world):
    code = """
        import sys
        print("崩溃前的最后一句话", file=sys.stderr)
        sys.exit(3)
    """
    v = await sub_version(world, py(code))
    s, t = await world.chat(v, "x")
    assert t.status == "error" and t.error.code == "AGENT_ERROR" and "退出码 3" in t.error.message
    assert "崩溃前的最后一句话" in t.error.message
    logs = [e.payload["message"] for e in await world.events(s.id) if e.type == "log"]
    assert logs == ["崩溃前的最后一句话"]


async def test_nonzero_exit_with_output_is_ok_with_warning(world):
    code = """
        import json, sys
        print(json.dumps({"output": {"role": "assistant", "content": "有结果"}}))
        sys.exit(1)
    """
    v = await sub_version(world, py(code))
    s, t = await world.chat(v, "x")
    assert t.status == "ok"
    assert any("退出码为 1" in e.payload["message"] for e in await world.events(s.id) if e.type == "log")


async def test_stderr_flood_is_truncated(world):
    code = """
        import json, sys
        for i in range(2000):
            print(f"warn {i}", file=sys.stderr)
        print(json.dumps({"output": {"role": "assistant", "content": "ok"}}))
    """
    v = await sub_version(world, py(code))
    s, t = await world.chat(v, "x")
    assert t.status == "ok"
    logs = [e for e in await world.events(s.id) if e.type == "log"]
    assert len(logs) == 501 and "输出过多" in logs[-1].payload["message"]


async def test_command_not_found_and_missing_cwd(world, tmp_path):
    v = await sub_version(world, ["/nonexistent/binary-xyz"])
    _, t = await world.chat(v, "x")
    assert t.status == "error" and t.error.code == "AGENT_START_FAILED" and "binary-xyz" in t.error.message
    v = await sub_version(
        world, py("pass"), subprocess={"command": py("pass"), "cwd": str(tmp_path / "nope")}
    )
    _, t = await world.chat(v, "x")
    assert (
        t.status == "error" and t.error.code == "AGENT_START_FAILED" and "工作目录不存在" in t.error.message
    )


async def test_python_command_falls_back_to_current_interpreter(world, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    v = await sub_version(
        world,
        [
            "python",
            "-c",
            'import json; print(json.dumps({"output": {"role": "assistant", "content": "py"}}))',
        ],
    )
    _, t = await world.chat(v, "x")
    assert t.status == "ok" and t.output.final.content == "py"


# ---------------------------------------------------------------- 超时 / 取消：进程树被杀掉


async def test_timeout_kills_process_tree(world, tmp_path):
    pidfile = tmp_path / "pids"
    v = await world.subprocess_version(env={"AGENT_PIDFILE": str(pidfile)}, runtime={"turn_timeout_s": 1.5})
    t0 = time.monotonic()
    s, t = await world.chat(v, "spawn:60")
    assert time.monotonic() - t0 < 10
    assert t.status == "timeout" and t.error.code == "TIMEOUT"
    parent, child = (int(x) for x in pidfile.read_text().split())
    assert not alive(parent) and not alive(child)
    assert (await world.types(s.id))[-2:] == ["error", "turn.end"]


async def test_cancel_kills_process_tree(world, tmp_path):
    pidfile = tmp_path / "pids"
    v = await world.subprocess_version(env={"AGENT_PIDFILE": str(pidfile)})
    s = await world.session(v)
    turn = await world.sessions.run_turn(s.id, "spawn:60", wait=False)
    parent, child = (int(x) for x in (await wait_file(pidfile)).split())
    assert alive(parent) and alive(child)
    t0 = time.monotonic()
    await world.sessions.cancel_turn(turn.id)
    assert time.monotonic() - t0 < 5
    t = await world.sessions.get_turn(turn.id)
    assert t.status == "cancelled"
    assert not alive(parent) and not alive(child)
    assert (await world.sessions.get(s.id)).session.status == "idle"


async def test_caller_cancellation_kills_process_tree(world, tmp_path):
    """wait=True 的调用方任务被取消时，本轮被取消，进程树被杀掉。"""
    pidfile = tmp_path / "pids"
    v = await world.subprocess_version(env={"AGENT_PIDFILE": str(pidfile)})
    s = await world.session(v)
    task = asyncio.create_task(world.sessions.run_turn(s.id, "spawn:60", wait=True))
    parent, child = (int(x) for x in (await wait_file(pidfile)).split())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not alive(parent) and not alive(child)
    assert not world.sessions.is_running(s.id)
    turns = (await world.sessions.get(s.id)).turns
    assert turns[0].status == "cancelled"


# ---------------------------------------------------------------- 健康检查


async def test_healthcheck(tmp_path):
    def adapter(**sub):
        return SubprocessAdapter(parse_spec({"kind": "code", "transport": "subprocess", "subprocess": sub}))

    r = await adapter(command=[sys.executable, "x.py"], cwd=str(tmp_path)).healthcheck()
    assert r.ok and r.details["cwd"] == str(tmp_path.resolve())
    r = await adapter(command=["definitely-not-a-command-xyz"]).healthcheck()
    assert not r.ok and "找不到可执行文件" in r.message
    r = await adapter(command=[sys.executable], cwd=str(tmp_path / "missing")).healthcheck()
    assert not r.ok and "工作目录不存在" in r.message
