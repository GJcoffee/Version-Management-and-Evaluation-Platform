"""用 evalp-sdk 写的示例 agent（HTTP 与子进程两种运行方式共用）。

按输入文本的前缀决定行为，便于测试各种场景：

- ``sleep:N``：睡 N 秒后回复（超时 / 取消测试）；
- ``spawn:N``：启动一个孙进程（``sleep``）并把 pid 写入环境变量 ``AGENT_PIDFILE`` 指向的文件，再睡 N 秒；
- 环境变量 ``AGENT_SLOW=秒``：每一轮先睡这么久；
- ``burst:N``：快速产生 N 个 span（SSE 回放 / 实时竞态测试）；
- ``env:NAME``：回复环境变量；``cwd``：回复工作目录；``boom``：抛异常；
- ``var:NAME``：回复变量 NAME 的值；``tools``：调用 tool_call 并使用 mock；
- ``llm``：通过 ``AGENT_LLM_URL`` 指向的 OpenAI 兼容服务（Fake OpenAI）发起一次 chat 调用并回复其内容；
- 其他：回复 ``echo[历史条数]: 输入``，同时产生 span / llm_call / tool_call 事件。

直接运行：``python sdk_agents.py stdio`` 或 ``python sdk_agents.py http PORT``。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.request

from evalp_sdk import TurnContext, agent


def _chat(url: str, text: str) -> dict:
    body = json.dumps({"model": "fake-model", "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(
        f"{url}/chat/completions", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
        return json.loads(r.read())


@agent
async def handle(ctx: TurnContext):
    text = ctx.input.text()
    if os.environ.get("AGENT_SLOW"):  # 每一轮先睡一会儿（协议自检的超时 / 取消探测）
        await asyncio.sleep(float(os.environ["AGENT_SLOW"]))
    if text.startswith("sleep:"):
        await asyncio.sleep(float(text.split(":", 1)[1]))
        return "slept"
    if text.startswith("spawn:"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
        path = os.environ.get("AGENT_PIDFILE")
        if path:
            with open(path, "w") as f:
                f.write(f"{os.getpid()} {child.pid}")
        await asyncio.sleep(float(text.split(":", 1)[1]))
        return "spawned"
    if text.startswith("burst:"):
        for i in range(int(text.split(":", 1)[1])):
            async with ctx.span("tick", i=i):
                pass
            if i % 20 == 0:
                await asyncio.sleep(0.005)
        return "burst done"
    if text.startswith("env:"):
        return str(os.environ.get(text.split(":", 1)[1]))
    if text == "cwd":
        return os.getcwd()
    if text == "boom":
        raise RuntimeError("agent 内部出错了")
    if text.startswith("var:"):
        return str(ctx.variables.get(text.split(":", 1)[1]))
    if text == "tools":
        async with ctx.tool_call("query_order", {"order_id": "A001"}) as t:
            t.result(t.mock_output if t.has_mock else {"status": "未 mock"})
            return f"工具输出: {json.dumps(t._output, ensure_ascii=False)}"
    if text == "llm":
        url = os.environ["AGENT_LLM_URL"]
        msgs = [{"role": "user", "content": "你好"}]
        async with ctx.llm_call(model="fake-model", provider="fake", messages=msgs) as call:
            data = await asyncio.to_thread(_chat, url, "你好")
            call.record(
                content=data["choices"][0]["message"]["content"],
                usage=data["usage"],
                finish_reason=data["choices"][0]["finish_reason"],
            )
        return data["choices"][0]["message"]["content"]
    print("这行 print 不能污染协议")
    async with ctx.span("retrieve", query=text) as s:
        s.set(n=2)
    async with ctx.llm_call(model="demo", messages=[{"role": "user", "content": text}]) as call:
        call.record({"content": "内部推理", "usage": {"prompt_tokens": 11, "completion_tokens": 7}})
    async with ctx.tool_call("lookup", {"q": text}) as t:
        t.result({"hits": 2})
    ctx.log("done")
    return f"echo[{len(ctx.history)}]: {text}"


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if mode == "http":
        handle.serve_http(port=int(sys.argv[2]))
    else:
        raise SystemExit(handle.run_stdio())
