"""业务异常与错误码（详设 11.2）。

所有异常继承 EvalpError(code, message, status, details)，由全局异常处理器统一输出
``{"error": {"code", "message", "details"}}``。
"""

from __future__ import annotations

from typing import Any


class EvalpError(Exception):
    """平台业务异常基类。"""

    default_code = "INTERNAL_ERROR"
    default_status = 500
    default_message = "内部错误"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        *,
        code: str | None = None,
        status: int | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.message = message or self.default_message
        self.status = status or self.default_status
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """转为响应体 ``{"error": {...}}``。"""
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InternalError(EvalpError):
    """500 INTERNAL_ERROR：未预期的内部错误。"""


class ValidationFailed(EvalpError):
    """400 VALIDATION_ERROR：请求或 Spec 校验失败，details.errors 给出字段路径。"""

    default_code = "VALIDATION_ERROR"
    default_status = 400
    default_message = "请求校验失败"


class SpecInvalid(EvalpError):
    """400 SPEC_INVALID：Spec 业务校验失败（Provider、模型或资产不存在）。"""

    default_code = "SPEC_INVALID"
    default_status = 400
    default_message = "Spec 校验失败"


class GitDirty(EvalpError):
    """400 GIT_DIRTY：代码型 agent 工作区有未提交改动且不允许 dirty。"""

    default_code = "GIT_DIRTY"
    default_status = 400
    default_message = "工作区有未提交的改动"


class ConventionViolation(EvalpError):
    """CONVENTION_VIOLATION：违反了 agent 的发布 / 推送规范（400；与现有状态冲突时为 409）。

    message 用中文写明违反了哪条、怎么改；details 带结构化信息（``convention``、``agent`` 等）。
    """

    default_code = "CONVENTION_VIOLATION"
    default_status = 400
    default_message = "违反了 agent 的发布规范"


class Unauthorized(EvalpError):
    """401 UNAUTHORIZED：token 缺失或错误。"""

    default_code = "UNAUTHORIZED"
    default_status = 401
    default_message = "未授权：token 缺失或错误"


class Forbidden(EvalpError):
    """403 FORBIDDEN：拒绝访问（如非回环地址调用 bootstrap）。"""

    default_code = "FORBIDDEN"
    default_status = 403
    default_message = "禁止访问"


class NotFound(EvalpError):
    """404 NOT_FOUND：资源不存在。"""

    default_code = "NOT_FOUND"
    default_status = 404
    default_message = "资源不存在"


class Conflict(EvalpError):
    """409 通用冲突，可通过 code 参数指定具体错误码（默认 CONFLICT）。"""

    default_code = "CONFLICT"
    default_status = 409
    default_message = "资源冲突"


class DraftConflict(Conflict):
    """409 DRAFT_CONFLICT：草稿 revision 不一致。"""

    default_code = "DRAFT_CONFLICT"
    default_message = "草稿已被修改"


class SessionBusy(Conflict):
    """409 SESSION_BUSY：会话正在执行一轮。"""

    default_code = "SESSION_BUSY"
    default_message = "会话正在执行中"


class AssetInUse(Conflict):
    """409 ASSET_IN_USE：被引用的资产不能硬删除。"""

    default_code = "ASSET_IN_USE"
    default_message = "资产正被引用，无法删除"


class InvalidState(Conflict):
    """409 INVALID_STATE：状态机不允许这个操作。"""

    default_code = "INVALID_STATE"
    default_message = "当前状态不允许该操作"


class DatasetParseError(EvalpError):
    """422 DATASET_PARSE_ERROR：数据集解析失败，details.lines 给出出错的行号。"""

    default_code = "DATASET_PARSE_ERROR"
    default_status = 422
    default_message = "数据集解析失败"


class ProviderError(EvalpError):
    """502 PROVIDER_ERROR：模型服务返回了不可重试的错误。"""

    default_code = "PROVIDER_ERROR"
    default_status = 502
    default_message = "模型服务返回错误"


class AgentError(EvalpError):
    """502 AGENT_ERROR：代码型 agent 返回错误或违反协议。"""

    default_code = "AGENT_ERROR"
    default_status = 502
    default_message = "agent 返回错误"


class Timeout(EvalpError):
    """504 TIMEOUT：超时。"""

    default_code = "TIMEOUT"
    default_status = 504
    default_message = "操作超时"


ERROR_CLASSES: tuple[type[EvalpError], ...] = (
    ValidationFailed,
    SpecInvalid,
    GitDirty,
    Unauthorized,
    NotFound,
    DraftConflict,
    SessionBusy,
    AssetInUse,
    InvalidState,
    DatasetParseError,
    ProviderError,
    AgentError,
    Timeout,
)
