from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ModuleContext:
    claim_type: str
    prompt_namespace: str
    policy_terms_path: Path


class ClaimModule(Protocol):
    """
    业务模块接口（最小骨架）。
    - 用于隔离 prompts/条款/业务规则
    - 未来每种案件类型实现一个 module（目录），而不是堆在一个超大 py 里
    """

    name: str
    claim_type: str

    def get_context(self) -> ModuleContext: ...

