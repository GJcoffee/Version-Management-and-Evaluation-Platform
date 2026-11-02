"""轮间处理器的结构化结果。"""

from dataclasses import dataclass
from typing import Any


@dataclass
class ArtifactResult:
    url: str
    raw_text: str
    raw_sha256: str
    parsed: dict | list
    selected: Any
    content_pointer: str | None
