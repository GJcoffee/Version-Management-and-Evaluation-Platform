"""兼容层：实现已迁移到 ``evalp.runtime.adapters.platforms.hiagent``。

新代码请直接从平台包导入；本模块只保留旧导入路径，等业务桥迁移完成后删除。
"""

from evalp.runtime.adapters.platforms.hiagent.adapter import HiAgentAdapter, business_bridge
from evalp.runtime.adapters.platforms.hiagent.config import BusinessBridgeConfig, HiAgentConfig
from evalp.runtime.adapters.platforms.hiagent.mapper import HiAgentMapper
from evalp.runtime.adapters.platforms.hiagent.sse import decoded, sse_frames

__all__ = [
    "BusinessBridgeConfig",
    "HiAgentAdapter",
    "HiAgentConfig",
    "HiAgentMapper",
    "business_bridge",
    "decoded",
    "sse_frames",
]
