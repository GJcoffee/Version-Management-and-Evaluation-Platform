"""适配器接入环境：全平台只有生产和测试两套。"""

from typing import Literal

Environment = Literal["production", "test"]
EnvironmentDescription = "接入环境：production（默认）或 test；测试未配置或与生产相同时落到生产"
