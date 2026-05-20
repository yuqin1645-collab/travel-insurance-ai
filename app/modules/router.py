from __future__ import annotations

from typing import Dict, Union


def detect_claim_type(benefit: Union[str, Dict] = "", folder_hint: str = "") -> str:
    """根据 BenefitName 和目录路径提示检测案件险种类型。

    兼容两种调用方式：
    - detect_claim_type("航班延误") → "flight_delay"
    - detect_claim_type({"BenefitName": "行李延误"}) → "baggage_delay"
    """
    if isinstance(benefit, dict):
        benefit = str(benefit.get("BenefitName") or "")
    benefit = str(benefit or "")
    folder_hint = str(folder_hint or "")
    combined = f"{benefit} {folder_hint}"
    if "行李延误" in combined:
        return "baggage_delay"
    if "航班延误" in combined or "flight_delay" in combined.lower():
        return "flight_delay"
    return "baggage_damage"