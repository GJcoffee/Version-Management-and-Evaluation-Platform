"""ToolSet / http / python / mock 工具。"""

import json
import os
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest
import respx
from evalp.errors import ValidationFailed
from evalp.runtime.messages import ToolCall
from evalp.runtime.tools.definition import HttpImpl, MockImpl, MockResponse, PythonImpl, ToolDefinition
from evalp.runtime.tools.executor import BuiltinTool, ToolSet
from evalp.runtime.tools.mock_tool import subset_match
from evalp.runtime.tools.result import MAX_TEXT_CHARS, ToolResult

FIXTURES = str(Path(__file__).resolve().parent.parent / "fixtures")
SCHEMA = {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}


def http_tool(name="query_order", **impl) -> ToolDefinition:
    impl = {"url": "http://orders.test/query", **impl}
    return ToolDefinition(
        name=name, description="查订单", parameters=SCHEMA, impl=HttpImpl(type="http", **impl)
    )


def py_tool(entry: str, name="pytool", **impl) -> ToolDefinition:
    impl.setdefault("workdir", FIXTURES)
    return ToolDefinition(name=name, impl=PythonImpl(type="python", entry=entry, **impl))


def mock_tool(responses, name="mocky") -> ToolDefinition:
    return ToolDefinition(name=name, impl=MockImpl(type="mock", responses=responses))


def call(name, arguments, id="c1") -> ToolCall:
    return ToolCall(id=id, name=name, arguments=arguments)


# ------------------------------------------------------------------ ToolResult / ToolSet 基础


def test_as_text_variants():
    assert ToolResult(output="plain").as_text() == "plain"
    assert ToolResult(output={"状态": "已发货", "n": 1}).as_text() == '{"状态": "已发货", "n": 1}'
    assert ToolResult(output=[1, "二"]).as_text() == '[1, "二"]'
    assert ToolResult(output=None).as_text() == ""
    assert ToolResult(output=3).as_text() == "3"
    assert ToolResult(output={"o": object}).as_text().startswith('{"o": "<class')


def test_as_text_truncates():
    text = ToolResult(output="字" * (MAX_TEXT_CHARS + 100)).as_text()
    assert text.endswith("[truncated]") and text.startswith("字" * 100)
    assert len(text) == MAX_TEXT_CHARS + len("\n[truncated]")
    exact = ToolResult(output="a" * MAX_TEXT_CHARS).as_text()
    assert "[truncated]" not in exact
    big = ToolResult(output={"k": "v" * 20000}).as_text()
    assert big.endswith("[truncated]")


async def test_schemas_and_names():
    ts = ToolSet.build(
        [http_tool(), mock_tool([MockResponse(default=1)]), BuiltinTool(sources=[("kb", "x", "h")])]
    )
    assert ts.names == ["query_order", "mocky", "knowledge_search"]
    s = ts.schemas()
    assert s[0] == {
        "type": "function",
        "function": {"name": "query_order", "description": "查订单", "parameters": SCHEMA},
    }
    assert (
        s[2]["function"]["name"] == "knowledge_search"
        and "query" in s[2]["function"]["parameters"]["properties"]
    )
    assert "mocky" in ts and len(ts) == 3
    assert ToolSet.build([]).schemas() == []
    await ts.aclose()


def test_duplicate_names_rejected():
    with pytest.raises(ValidationFailed):
        ToolSet.build([http_tool(), http_tool()])


async def test_unknown_tool_returns_error_result():
    ts = ToolSet.build([http_tool()])
    r = await ts.execute(call("nope", {}), timeout=5)
    assert r.is_error and "未知工具: nope" in r.output and "query_order" in r.output
    assert r.mocked is False and r.latency_ms >= 0


@pytest.mark.parametrize(
    ("arguments", "needle"),
    [
        ('{"order_id": "A1"', "不是合法的 JSON"),
        ("not json at all", "不是合法的 JSON"),
        ("[1, 2]", "必须是 JSON 对象"),
    ],
)
async def test_invalid_arguments_return_error_result(arguments, needle):
    ts = ToolSet.build([mock_tool([MockResponse(default="never")])])
    r = await ts.execute(call("mocky", arguments), timeout=5)
    assert r.is_error and needle in r.output


async def test_string_arguments_that_are_valid_json_are_parsed():
    ts = ToolSet.build([mock_tool([MockResponse(match={"a": 1}, output="hit")])])
    assert (await ts.execute(call("mocky", '{"a": 1}'))).output == "hit"
    assert (await ts.execute(call("mocky", ""))).is_error  # 空参数 -> {}，无匹配且无 default


async def test_aclose_and_context_manager():
    async with ToolSet.build([http_tool()]) as ts:
        assert len(ts) == 1


# ------------------------------------------------------------------ http 工具


@respx.mock
async def test_http_default_json_body_and_json_response():
    route = respx.post("http://orders.test/query").mock(
        return_value=httpx.Response(200, json={"状态": "已发货"})
    )
    ts = ToolSet.build([http_tool()])
    r = await ts.execute(call("query_order", {"order_id": "A001"}), timeout=5)
    assert not r.is_error and r.output == {"状态": "已发货"} and not r.mocked
    assert json.loads(route.calls[0].request.content) == {"order_id": "A001"}
    await ts.aclose()


@respx.mock
async def test_http_env_substitution_and_body_template(monkeypatch):
    monkeypatch.setenv("ORDER_HOST", "orders.test")
    monkeypatch.setenv("ORDER_TOKEN", "t-123")
    route = respx.put("http://orders.test/v2/query").mock(return_value=httpx.Response(200, text="plain 文本"))
    ts = ToolSet.build(
        [
            http_tool(
                method="PUT",
                url="http://${env:ORDER_HOST}/v2/query",
                headers={"X-Token": "${env:ORDER_TOKEN}"},
                body_template='{"id": {{ args.order_id | tojson }}}',
            )
        ]
    )
    r = await ts.execute(call("query_order", {"order_id": 'A"1'}), timeout=5)
    assert r.output == "plain 文本" and not r.is_error
    req = route.calls[0].request
    assert req.headers["x-token"] == "t-123" and req.headers["content-type"] == "application/json"
    assert json.loads(req.content) == {"id": 'A"1'}


@respx.mock
async def test_http_missing_env_is_error_result(monkeypatch):
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    ts = ToolSet.build([http_tool(headers={"X-Token": "${env:NOPE_TOKEN}"})])
    r = await ts.execute(call("query_order", {"order_id": "1"}))
    assert r.is_error and "NOPE_TOKEN" in r.output


@respx.mock
async def test_http_non_2xx_is_error():
    respx.post("http://orders.test/query").mock(return_value=httpx.Response(404, text="订单不存在"))
    r = await ToolSet.build([http_tool()]).execute(call("query_order", {"order_id": "1"}))
    assert r.is_error and r.output == "HTTP 404: 订单不存在" and r.detail == {"status": 404}
    respx.post("http://orders.test/query").mock(return_value=httpx.Response(500, text="x" * 5000))
    r = await ToolSet.build([http_tool()]).execute(call("query_order", {"order_id": "1"}))
    assert r.is_error and len(r.output) < 2100


@respx.mock
async def test_http_get_uses_query_params():
    route = respx.get("http://orders.test/query").mock(return_value=httpx.Response(200, json=[1, 2]))
    ts = ToolSet.build([http_tool(method="GET")])
    r = await ts.execute(call("query_order", {"order_id": "A 1", "tags": ["x", "y"]}))
    assert r.output == [1, 2]
    assert route.calls[0].request.url.params["order_id"] == "A 1"
    assert route.calls[0].request.url.params["tags"] == '["x", "y"]'
    assert route.calls[0].request.content == b""


@respx.mock
async def test_http_network_errors_and_timeout():
    respx.post("http://orders.test/query").mock(side_effect=httpx.ConnectError("refused"))
    r = await ToolSet.build([http_tool()]).execute(call("query_order", {"order_id": "1"}))
    assert r.is_error and "ConnectError" in r.output
    respx.post("http://orders.test/query").mock(side_effect=httpx.ReadTimeout("slow"))
    r = await ToolSet.build([http_tool(timeout_s=3)]).execute(call("query_order", {"order_id": "1"}))
    assert r.is_error and "超时" in r.output


@respx.mock
async def test_http_template_errors_are_error_results():
    ts = ToolSet.build(
        [
            http_tool("undef", body_template="{{ args.missing }}"),
            http_tool("syntax", body_template="{{ args.x "),
            http_tool("evil", body_template="{{ args.__class__.__mro__ }}"),
        ]
    )
    for name in ("undef", "syntax", "evil"):
        r = await ts.execute(call(name, {"order_id": "1"}))
        assert r.is_error and "body_template" in r.output, name


@respx.mock
async def test_http_empty_body_ok():
    respx.post("http://orders.test/query").mock(return_value=httpx.Response(204))
    r = await ToolSet.build([http_tool()]).execute(call("query_order", {"order_id": "1"}))
    assert not r.is_error and r.output == ""


# ------------------------------------------------------------------ python 工具


async def test_python_tool_success_variants():
    ts = ToolSet.build(
        [
            py_tool("sample_tools:echo", "echo"),
            py_tool("sample_tools:greet", "greet"),
            py_tool("sample_tools:async_double", "dbl"),
        ]
    )
    assert (await ts.execute(call("echo", {"a": [1, "二"]}), timeout=30)).output == {"echo": {"a": [1, "二"]}}
    assert (await ts.execute(call("greet", {"name": "世界"}))).output == "你好，世界"
    assert (await ts.execute(call("dbl", {"n": 4}))).output == 8


async def test_python_tool_exception():
    r = await ToolSet.build([py_tool("sample_tools:boom")]).execute(call("pytool", {"why": "x"}), timeout=30)
    assert r.is_error and r.output == "ValueError: 出错了: x"
    assert "Traceback" in r.detail["traceback"]


async def test_python_tool_print_does_not_pollute():
    r = await ToolSet.build([py_tool("sample_tools:noisy")]).execute(call("pytool", {}), timeout=30)
    assert not r.is_error and r.output == "done"
    assert "这行 print" in r.detail["stderr"]


async def test_python_tool_bad_entry_and_bad_interpreter():
    r = await ToolSet.build([py_tool("sample_tools:nope")]).execute(call("pytool", {}), timeout=30)
    assert r.is_error and "AttributeError" in r.output
    r = await ToolSet.build([py_tool("sample_tools:echo", python="/no/such/python")]).execute(
        call("pytool", {})
    )
    assert r.is_error and "spawn" in r.output


async def test_python_tool_timeout_kills_process_tree(tmp_path):
    pidfile = tmp_path / "child.pid"
    ts = ToolSet.build([py_tool("sample_tools:spawn_and_sleep", timeout_s=1.5)])
    start = time.monotonic()
    r = await ts.execute(call("pytool", {"pidfile": str(pidfile)}), timeout=30)
    assert time.monotonic() - start < 15
    assert r.is_error and "超时" in r.output
    child_pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and _running(child_pid):
        time.sleep(0.05)
    assert not _running(child_pid), "孙子进程应随进程树一起被杀"


def _running(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def test_python_tool_caller_timeout_is_min_of_both(tmp_path):
    ts = ToolSet.build([py_tool("sample_tools:sleeper", timeout_s=60)])
    start = time.monotonic()
    r = await ts.execute(call("pytool", {"seconds": 30}), timeout=1)
    assert r.is_error and "超时" in r.output and time.monotonic() - start < 10


async def test_python_tool_cancel_kills_process(tmp_path):
    import asyncio

    pidfile = tmp_path / "c.pid"
    ts = ToolSet.build([py_tool("sample_tools:spawn_and_sleep", timeout_s=60)])
    task = asyncio.create_task(ts.execute(call("pytool", {"pidfile": str(pidfile)})))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _running(pid):
            break
        await asyncio.sleep(0.1)
    assert not _running(pid)


async def test_python_tool_custom_interpreter_gets_toolhost_shim(tmp_path):
    """用户指定的解释器未必装了 evalp：通过 PYTHONPATH 下的 shim 目录仍可 -m evalp.toolhost。"""
    link = tmp_path / "py"
    os.symlink(sys.executable, link)  # 路径不同于 sys.executable -> 走 shim
    ts = ToolSet.build([py_tool("sample_tools:greet", python=str(link))])
    r = await ts.execute(call("pytool", {"name": "shim"}), timeout=30)
    assert r.output == "你好，shim", r


async def test_python_tool_inherits_env_and_workdir_is_cwd(monkeypatch):
    monkeypatch.setenv("EVALP_TEST_VAR", "ok")
    ts = ToolSet.build([py_tool("sample_tools:read_env", "env"), py_tool("sample_tools:cwd_and_path", "cwd")])
    assert (await ts.execute(call("env", {"name": "EVALP_TEST_VAR"}), timeout=30)).output == "ok"
    out = (await ts.execute(call("cwd", {}), timeout=30)).output
    assert os.path.realpath(out["cwd"]) == os.path.realpath(FIXTURES)


# ------------------------------------------------------------------ mock


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ({"a": 1}, {"a": 1, "b": 2}, True),
        ({"a": 1}, {"a": 2}, False),
        ({"a": 1}, {}, False),
        ({"a": {"x": 1}}, {"a": {"x": 1, "y": 2}}, True),
        ({"a": {"x": 1}}, {"a": 1}, False),
        ({"a": True}, {"a": 1}, False),
        ({"a": [1, 2]}, {"a": [1, 2]}, True),
        ({"a": [1]}, {"a": [1, 2]}, False),
        ({}, {"anything": 1}, True),
        ({"s": "中"}, {"s": "中"}, True),
    ],
)
def test_subset_match(expected, actual, ok):
    assert subset_match(expected, actual) is ok


async def test_mock_tool_matching_order_and_default():
    ts = ToolSet.build(
        [
            mock_tool(
                [
                    MockResponse(match={"order_id": "A001"}, output={"status": "已发货"}),
                    MockResponse(match={"order_id": "A001", "x": 1}, output="never"),  # 前一条已命中
                    MockResponse(default={"status": "未找到"}),
                    MockResponse(match={"order_id": "B"}, output="B 订单"),
                ]
            )
        ]
    )
    r = await ts.execute(call("mocky", {"order_id": "A001", "extra": True}))
    assert r.output == {"status": "已发货"} and r.mocked and not r.is_error
    assert (await ts.execute(call("mocky", {"order_id": "B"}))).output == "B 订单"  # default 位置不影响优先级
    r = await ts.execute(call("mocky", {"order_id": "zzz"}))
    assert r.output == {"status": "未找到"} and r.mocked


async def test_mock_tool_no_match_no_default_is_error():
    ts = ToolSet.build([mock_tool([MockResponse(match={"a": 1}, output="x")])])
    r = await ts.execute(call("mocky", {"a": 2}))
    assert r.is_error and "没有匹配" in r.output


async def test_case_mocks_override_real_tools():
    ts = ToolSet.build(
        [http_tool(), py_tool("sample_tools:boom", "other")],
        mocks={
            "query_order": [
                {"match": {"order_id": "A001"}, "output": {"status": "已发货"}},
                MockResponse(default={"status": "未找到"}),
            ],
            "not_in_set": [MockResponse(default=1)],  # 工具集中没有的名字被忽略
        },
    )
    assert ts.names == ["query_order", "other"]
    assert ts.schemas()[0]["function"]["parameters"] == SCHEMA  # schema 保持不变
    with respx.mock:  # 不会发 HTTP
        r = await ts.execute(call("query_order", {"order_id": "A001"}))
        assert r.output == {"status": "已发货"} and r.mocked and ts.is_mocked("query_order")
        assert (await ts.execute(call("query_order", {"order_id": "Z"}))).output == {"status": "未找到"}
    r = await ts.execute(call("other", {}), timeout=30)
    assert r.is_error and not r.mocked and not ts.is_mocked("other")


async def test_mocks_can_override_builtin():
    ts = ToolSet.build(
        [BuiltinTool(sources=[("kb", "内容", "h")])],
        mocks={
            "knowledge_search": [MockResponse(default=[{"source": "m", "chunk_id": "m#0", "text": "mock"}])]
        },
    )
    r = await ts.execute(call("knowledge_search", {"query": "x"}))
    assert r.mocked and r.output[0]["text"] == "mock"
