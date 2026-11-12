"""HiAgent SSE → 规范事件：调用身份来自上游 ID，思考阶段不冒充确定的 LLM 调用。"""

from typing import Any

from evalp.errors import AgentError
from evalp.runtime.adapters.platforms.hiagent.config import HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.sse import decoded
from evalp.runtime.events import AgentEvent, ev, redact


class HiAgentMapper:
    """调用身份来自上游 ID；思考阶段暂不声称是确定的 LLM 调用。"""

    def __init__(self, config: HiAgentConfig) -> None:
        self.config = config
        self.message_id: str | None = None
        self.conversation_id: str | None = None
        self.done = False
        self.calls: dict[str, dict] = {}
        self.stages: dict[str, dict] = {}
        self.current: str | None = None
        self.final_parts: list[str] = []
        self.usage: dict[str, Any] = {}
        self.seen: set[str] = set()

    def feed(self, frame: dict[str, Any]) -> list[AgentEvent]:
        """输出正文、思考和工具；整轮用量保留其范围，不分摊。"""
        eid = frame.get("_event_id")
        if eid and eid in self.seen:
            return []
        if eid:
            self.seen.add(eid)
        kind = frame.get("event")
        first_message = self.message_id is None
        mid = frame.get("message_id")
        if not mid and kind in ("message_start", "message_end"):
            mid = frame.get("id")
        if mid:
            if self.message_id and str(mid) != self.message_id:
                raise AgentError("SSE 消息身份改变")
            self.message_id = str(mid)
        conversation = frame.get("conversation_id") or frame.get("app_conversation_id")
        if conversation:
            if self.conversation_id and conversation != self.conversation_id:
                raise AgentError("SSE 会话身份改变")
            self.conversation_id = str(conversation)
        if frame.get("app_version") and str(frame["app_version"]) != self.config.published_version:
            raise AgentError("SSE 发布版本与预期不一致")
        events = [ev("source.raw", source="hiagent_sse", frame=redact(frame))]
        if first_message and self.message_id:
            # 收到身份即保存，后续执行或观测失败仍可用同一消息重查。
            events.append(ev("metrics", scope="turn", upstream_message_id=self.message_id,
                             upstream={"message_id": self.message_id, "app_id": self.config.app_id,
                                       "expected_version": self.config.published_version}))
        if kind == "think_message_output_start":
            identity = frame.get("id")
            if not identity:
                raise AgentError("思考阶段缺少上游标识")
            self.current = str(identity)
            if self.current not in self.stages:
                self.stages[self.current] = {"text": [], "reasoning": [], "step": f"stage:{identity}"}
                events.append(ev(
                    "span.start", step_id=f"stage:{identity}", name="上游输出阶段",
                    attrs={"source": "hiagent_sse", "association": "unknown", "upstream_id": identity},
                ))
        elif kind == "message_output_start":
            identity = frame.get("think_message_id") or frame.get("id")
            self.current = str(identity) if identity else None
        elif kind in ("think_message", "think_message_output", "message"):
            identity = frame.get("think_message_id") or self.current
            stage = self.stages.get(str(identity)) if identity else None
            text = frame.get("answer") or frame.get("content") or frame.get("text")
            if isinstance(text, str):
                reasoning = kind != "message"
                if stage:
                    stage["reasoning" if reasoning else "text"].append(text)
                if not reasoning:
                    self.final_parts.append(text)
                events.append(ev(
                    "llm.reasoning" if reasoning else "llm.delta",
                    step_id=stage["step"] if stage else "output:unknown",
                    text=text, source="hiagent_sse", association="unknown",
                ))
        elif kind == "agent_thought" and frame.get("tool"):
            cid = str(frame.get("id") or "")
            if not cid:
                raise AgentError("工具请求缺少调用 ID")
            args = decoded(frame.get("tool_input") or {})
            call = {"name": frame["tool"], "arguments": args, "step": f"tool:{cid}"}
            if cid in self.calls:
                if self.calls[cid] != call:
                    raise AgentError("同一工具调用 ID 的内容发生变化")
                return events
            self.calls[cid] = call
            events.append(ev(
                "tool.call", step_id=call["step"], call_id=cid, name=call["name"],
                arguments=redact(args), source="hiagent_sse",
                protocol=frame.get("protocol") or "unknown", association="unknown",
            ))
        elif kind == "agent_thought_end":
            cid = str(frame.get("id") or "")
            call = self.calls.get(cid)
            if call is None:
                raise AgentError("工具结果没有对应调用")
            latency = frame.get("tool_latency")
            events.append(ev(
                "tool.result", step_id=call["step"], call_id=cid, name=call["name"],
                output=redact(decoded(frame.get("observation", frame.get("tool_output")))),
                is_error=bool(frame.get("error") or frame.get("error_msg") or frame.get("error_code")),
                latency_ms=(
                    round(latency * 1000) if isinstance(latency, int | float) and latency >= 0 else None
                ),
                source="hiagent_sse",
            ))
        elif kind == "message_end":
            self.done = True
            usage = frame.get("usage") or frame
            self.usage = {
                "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
                "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
                "scope": "turn", "source": "hiagent_sse", "observation_status": "partial",
            }
            events.append(ev("metrics", scope="turn", source="hiagent_sse", usage=self.usage,
                             execution_status="completed"))
        elif kind in ("error", "message_error"):
            raise AgentError("HiAgent 返回执行失败")
        return events
