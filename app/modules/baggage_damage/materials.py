from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_material_gate_early_return(
    *,
    forceid: str,
    material_result: Dict[str, Any],
    ctx: Dict[str, Any],
    ensure_purchase_proof: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    材料门禁：把“缺件/无效件/关键材料缺失(如购买凭证)”统一在这里早退。
    返回：
    - dict: 直接作为 review_claim_async 的返回值（补件）
    - None: 表示材料门禁通过，可继续后续阶段
    """

    missing: List[Any] = material_result.get("missing_materials", []) or []
    invalid: List[Any] = material_result.get("invalid_materials", []) or []
    needs_manual = bool(material_result.get("needs_manual_review", False))

    # 购买凭证/发票：若 vision 证据明确未提交，则直接补件
    if ensure_purchase_proof:
        present_flags = material_result.get("present_flags") if isinstance(material_result, dict) else None
        has_purchase_proof = bool((present_flags or {}).get("purchase_proof")) if isinstance(present_flags, dict) else False
        if not has_purchase_proof:
            if not any(("购买凭证" in str(x)) or ("发票" in str(x)) for x in missing):
                missing.append("购买凭证/发票/收据（用于核定受损财产原价）")
            return {
                "forceid": forceid,
                "Remark": "需要补充材料: 购买凭证/发票/收据（用于核定受损财产原价）",
                "IsAdditional": "Y",
                "KeyConclusions": [
                    {
                        "checkpoint": "材料完整性核对",
                        "Eligible": "N",
                        "Remark": "缺少购买凭证/发票/收据，无法核定原价。",
                    }
                ],
                "DebugInfo": ctx,
            }

    # 真缺件/无效件：直接补件（仅 needs_manual_review 不应阻断）
    if missing or invalid or (not material_result.get("is_complete", False) and not needs_manual):
        return {
            "forceid": forceid,
            "Remark": (
                f"需要补充材料: {', '.join([str(x) for x in missing])}"
                if missing
                else ("需要补充材料: 已提交材料存在无效/不清晰项，请补充清晰版本" if invalid else "需要补充材料")
            ),
            "IsAdditional": "Y",
            "KeyConclusions": [
                {
                    "checkpoint": "材料完整性核对",
                    "Eligible": "N",
                    "Remark": material_result.get("reason", ""),
                }
            ],
            "DebugInfo": ctx,
        }

    return None

