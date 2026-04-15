from __future__ import annotations

from app.config import config
from app.modules.base import ClaimModule, ModuleContext


class BaggageDelayModule:
    """
    行李延误模块（MVP）：
    - prompts 命名空间：baggage_delay（后续可按阶段补充 prompt）
    - 条款独立到 static/旅行险条款/baggage_delay/行李延误保险条款.txt
    """

    name = "行李延误"
    claim_type = "baggage_delay"

    def get_context(self) -> ModuleContext:
        return ModuleContext(
            claim_type=self.claim_type,
            prompt_namespace=self.claim_type,
            policy_terms_path=(
                config.POLICY_TERMS_DIR / "baggage_delay" / "行李延误保险条款.txt"
            ),
        )


MODULE: ClaimModule = BaggageDelayModule()
