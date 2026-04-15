from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.config import config


@dataclass(frozen=True)
class PolicyTermsRegistry:
    """
    条款选择注册表（最小骨架）。
    “案件类型 <-> 服务手册目录名称”映射做好后，再在这里扩展多类型映射。
    """

    def resolve(self, claim_type: str) -> Path:
        if claim_type == "baggage_damage":
            # 强约束：随身财产条款必须来自 baggage_damage 命名空间目录
            p = config.POLICY_TERMS_DIR / "baggage_damage" / "个人随身物品保险条款.txt"
            return p
        if claim_type == "flight_delay":
            # 预留：航班延误条款（若不存在，调用方可选择兜底默认阈值/限额）
            p = config.POLICY_TERMS_DIR / "flight_delay" / "航班延误保险条款.txt"
            return p
        if claim_type == "baggage_delay":
            p = config.POLICY_TERMS_DIR / "baggage_delay" / "行李延误保险条款.txt"
            return p
        raise ValueError(f"当前未配置该案件类型的条款映射: {claim_type}")


POLICY_TERMS = PolicyTermsRegistry()

