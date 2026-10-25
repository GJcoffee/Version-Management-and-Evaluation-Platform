# evalp-sdk

把你的 agent 接入 [EvalP](../docs/02-详细设计.md)（版本管理与评测平台）的辅助库。**零第三方依赖**，只用标准库，Python >= 3.11。

接入后，平台可以对话、评测、对比你的 agent，并在 trace 里看到每一次检索、模型调用和工具调用。

## 安装

```bash
pip install -e sdk          # 在仓库根目录
```

## 最小示例

```python
from evalp_sdk import agent, TurnContext


@agent
async def handle(ctx: TurnContext):
    return f"你说的是：{ctx.input.text()}"  # 返回 str 或 Message


if __name__ == "__main__":
    handle.serve_http(port=8001)  # HTTP 模式；或者 handle.run_stdio() 作为子进程模式
```

返回 `str` / `Message` 时，SDK 自动生成 `message.assistant` 和 `turn.end`；抛出异常时生成 `error` 和
`turn.end(status=error)`。handler 可以是 `async def`，也可以是普通函数（会放进线程执行）。

## TurnContext

| 属性 | 含义 |
|---|---|
| `ctx.input` | 本轮用户消息（`Message`，`.text()` 取纯文本） |
| `ctx.history` | 之前各轮的消息（`list[Message]`，平台按"每轮的 input + 最终回复"拼接） |
| `ctx.params` | Spec 里的 `params`（原样透传） |
| `ctx.variables` | 用例 / 会话变量 |
| `ctx.tool_mocks` | 评测用例的工具 mock（没有则 `None`） |
| `ctx.seed` | Spec 里的 seed |
| `ctx.session_id` / `ctx.turn_id` / `ctx.deadline_ms` | 会话、轮次 id 与剩余时间预算 |

## 记录步骤（可选，但强烈建议）

```python
async def handle(ctx):
    # 自定义步骤（如检索）：生成 span.start / span.end
    async with ctx.span("retrieve", query=ctx.input.text()) as s:
        docs = await retrieve(ctx.input.text())
        s.set(n=len(docs))

    # 模型调用：生成 llm.request / llm.response，用量会汇总到本轮
    async with ctx.llm_call(model="qwen-max", messages=msgs) as call:
        resp = await my_llm(msgs)
        call.record(resp)  # str、dict（content/usage/tool_calls…）或带 .text/.usage 的对象

    # 工具调用：生成 tool.call / tool.result
    async with ctx.tool_call("query_order", {"order_id": "A001"}) as t:
        t.result(t.mock_output if t.has_mock else await query_order("A001"))

    ctx.log("命中缓存", level="debug")
    return resp.text
```

`span` / `llm_call` / `tool_call` 同时支持 `with` 与 `async with`，嵌套关系自动记录。
评测用例带了工具 mock 时，`t.has_mock` 为真，`t.mock_output` 是命中的输出；用它调用 `t.result(...)` 会自动标记 `mocked`。

## 运行方式

**HTTP**（Spec 中 `transport: http`，`http.base_url` 指向它）：

```python
handle.serve_http(port=8001)  # 阻塞
server = handle.start_http(port=0)  # 后台线程，server.url / server.port / server.close()
```

提供 `POST /evalp/run`（流式 NDJSON 响应）、`GET /evalp/health`、`POST /evalp/cancel`。平台断开连接或调用
`/evalp/cancel` 时，进行中的 handler 会被取消（`asyncio.CancelledError`）。

**子进程**（Spec 中 `transport: subprocess`，`command: ["python", "my_agent.py"]`）：

```python
handle.run_stdio()
```

平台每轮启动一次进程：请求写入 stdin，事件从 stdout 逐行读取。SDK 会把 `print` 的输出重定向到 stderr
（平台记为 warn 级别的 log 事件），不会污染协议。

## 协议要点（`evalp-agent/1`）

- 请求：`{protocol, session_id, turn_id, history, input, params, variables, tool_mocks, seed, deadline_ms}`；
- 响应：L1 是单个 JSON `{"output": Message, "usage"?, "cost"?}`；L2 是 NDJSON，每行
  `{"type", "step_id"?, "parent_step_id"?, "ts"?, "payload"}`，事件类型见详设 6.2；SDK 走 L2；
- 平台只记录每轮的最终回复作为下一轮的历史；需要自己管理更复杂的内部状态时，请在 agent 内部维护。

## 自检

平台提供协议契约检查：`POST /api/v1/versions/{ref}/adapter-check`，会对接入的 agent 跑一组用例（健康检查、
单轮、多轮、超时、取消、事件合法性）并输出报告。
