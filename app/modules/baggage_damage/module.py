from __future__ import annotations

from pathlib import Path

from app.config import config
from app.modules.base import ClaimModule, ModuleContext


class BaggageDamageModule:
    """
    当前默认模块：随身财产（含承运人责任/托运行李损坏等）。
    现在你们所有案件都在这里，所以这里做“强约束”只加载个人随身物品条款。
    """

    name = "随身财产"
    claim_type = "baggage_damage"

    def get_context(self) -> ModuleContext:
        policy_terms_path = config.POLICY_TERMS_DIR / "baggage_damage" / "个人随身物品保险条款.txt"
        return ModuleContext(
            claim_type=self.claim_type,
            prompt_namespace=self.claim_type,
            policy_terms_path=policy_terms_path,
        )


MODULE: ClaimModule = BaggageDamageModule()

