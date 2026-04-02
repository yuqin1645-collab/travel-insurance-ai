from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from app.modules.base import ClaimModule
from app.modules.baggage_damage.module import MODULE as BAGGAGE_DAMAGE_MODULE
from app.modules.flight_delay.module import MODULE as FLIGHT_DELAY_MODULE


@dataclass(frozen=True)
class ModuleRegistry:
    """
    模块注册表：
    - 根据案件类型选择对应模块
    - 当前阶段：只启用随身财产模块，避免误加载其它类型的 prompts/条款
    """

    modules: Dict[str, ClaimModule]

    @classmethod
    def default(cls) -> "ModuleRegistry":
        return cls(
            modules={
                BAGGAGE_DAMAGE_MODULE.claim_type: BAGGAGE_DAMAGE_MODULE,
                FLIGHT_DELAY_MODULE.claim_type: FLIGHT_DELAY_MODULE,
            }
        )

    def get(self, claim_type: str) -> ClaimModule:
        if claim_type not in self.modules:
            raise KeyError(f"未注册的案件类型: {claim_type}")
        return self.modules[claim_type]


DEFAULT_REGISTRY = ModuleRegistry.default()

