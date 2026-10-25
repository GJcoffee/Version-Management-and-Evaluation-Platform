"""工具定义（详设 5.2）。tool 资产的内容解析为 ToolDefinition。

由总控定义的跨模块契约：资产模块用它校验 tool 资产，运行时用它执行工具。修改需记录 DEVIATIONS。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HttpImpl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["http"]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    headers: dict[str, str] = {}
    body_template: str | None = None  # Jinja2 模板，变量 args；为空时把参数作为 JSON body
    timeout_s: float = 30


class PythonImpl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["python"]
    entry: str  # "module.path:function"
    python: str | None = None  # 解释器路径，默认用平台自身的解释器
    workdir: str | None = None
    timeout_s: float = 30

    @field_validator("entry")
    @classmethod
    def _check_entry(cls, v: str) -> str:
        mod, sep, fn = v.partition(":")
        if not sep or not mod or not fn:
            raise ValueError("entry 必须是 'module:function' 格式")
        return v


class MockResponse(BaseModel):
    """match 与 default 二选一：match 为参数子集匹配。"""

    model_config = ConfigDict(extra="forbid")
    match: dict[str, Any] | None = None
    output: Any = None
    default: Any = None


class MockImpl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mock"]
    responses: list[MockResponse] = []


ToolImpl = Annotated[HttpImpl | PythonImpl | MockImpl, Field(discriminator="type")]


class ToolDefinition(BaseModel):
    """一个可被模型调用的工具。parameters 必须是 JSON Schema（type=object）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    impl: ToolImpl

    @field_validator("parameters")
    @classmethod
    def _check_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("type") != "object":
            raise ValueError("parameters 必须是 type=object 的 JSON Schema")
        return v

    def openai_schema(self) -> dict[str, Any]:
        """转换为 OpenAI tools 参数中的一项。"""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }
