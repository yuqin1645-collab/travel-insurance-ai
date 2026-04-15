from __future__ import annotations

from app.config import config
from app.modules.base import ClaimModule, ModuleContext


class FlightDelayModule:
    """
    航班延误模块（MVP）：
    - prompts 命名空间：flight_delay
    - 条款：后续接入真实条款文件；当前允许为空或走默认阈值/限额兜底
    """

    name = "航班延误"
    claim_type = "flight_delay"

    def get_context(self) -> ModuleContext:
        # 预留：未来把航班延误条款放在 static/旅行险条款/flight_delay/xxx.txt
        policy_terms_path = config.POLICY_TERMS_DIR / "flight_delay" / "航班延误保险条款.txt"
        return ModuleContext(
            claim_type=self.claim_type,
            prompt_namespace=self.claim_type,
            policy_terms_path=policy_terms_path,
        )


MODULE: ClaimModule = FlightDelayModule()

