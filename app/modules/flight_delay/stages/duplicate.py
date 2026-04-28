"""
flight_delay stages — 重复理赔检测。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# 已结案状态关键词
_CONCLUDED_STATUS_KEYWORDS = (
    "零结关案", "支付成功", "事后理赔拒赔",
    "取消理赔", "结案待财务付款",
    "approved", "rejected", "settled", "closed",
    "已赔付", "已结案", "已关闭",
)


def _is_concluded_status(status: Any) -> bool:
    """判断某个案件状态是否已结案。"""
    if status is None:
        return False
    s = str(status).strip().lower()
    if not s or s in ("", "null", "none", "unknown", "pending", "in_review", "待审", "审核中", "处理中"):
        return False
    return any(kw in s for kw in _CONCLUDED_STATUS_KEYWORDS)


def _is_same_event(current: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    """检查两个案件是否为同一事件。"""
    current_id = (current.get("ID_Number") or "").strip()
    candidate_id = (candidate.get("ID_Number") or "").strip()
    if not current_id or not candidate_id or current_id != candidate_id:
        return False

    current_product = (current.get("Product_Name") or "").strip()
    candidate_product = (candidate.get("Product_Name") or "").strip()
    if not current_product or not candidate_product or current_product != candidate_product:
        return False

    current_benefit = (current.get("BenefitName") or "").strip()
    candidate_benefit = (candidate.get("BenefitName") or "").strip()
    if not current_benefit or not candidate_benefit or current_benefit != candidate_benefit:
        return False

    current_date = (current.get("Date_of_Accident") or "").strip()[:10]
    candidate_date = (candidate.get("Date_of_Accident") or "").strip()[:10]
    if current_date and candidate_date and current_date != candidate_date:
        return False

    return True


def _check_duplicate_claim(
    claim_info: Dict[str, Any],
    forceid: str,
) -> Optional[Dict[str, Any]]:
    """检测重复理赔（SamePolicyClaim 字段）。"""
    same_policy = claim_info.get("SamePolicyClaim")
    if same_policy is None:
        return None

    if isinstance(same_policy, dict):
        same_policy = [same_policy]

    if not isinstance(same_policy, list) or not same_policy:
        return None

    concluded_match = None
    unconcluded_matches = []

    for item in same_policy:
        if not isinstance(item, dict):
            continue
        if not _is_same_event(claim_info, item):
            continue

        claim_id = (
            item.get("ClaimId") or item.get("claim_id")
            or item.get("CaseNo") or item.get("case_no")
            or item.get("Id") or item.get("id") or ""
        )
        claim_id = str(claim_id).strip()

        status = (
            item.get("Final_Status") or item.get("final_status")
            or item.get("Status") or item.get("status")
            or item.get("AuditResult") or item.get("audit_result")
            or item.get("Result") or item.get("result")
            or item.get("Conclusion") or item.get("conclusion")
        )

        if _is_concluded_status(status):
            if not concluded_match:
                concluded_match = (claim_id, status)
        else:
            unconcluded_matches.append((claim_id, status))

    base_info = {
        "ClaimId": claim_info.get("ClaimId") or "",
        "BenefitName": claim_info.get("BenefitName") or "",
        "applicant_name": (
            claim_info.get("Applicant_Name") or claim_info.get("ApplicantName")
            or claim_info.get("Insured_Name") or ""
        ),
        "insured_name": (
            claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name")
            or claim_info.get("Applicant_Name") or ""
        ),
        "passenger_id_type": claim_info.get("ID_Type") or claim_info.get("id_type") or "",
        "passenger_id_number": claim_info.get("ID_Number") or claim_info.get("id_number") or "",
    }

    if concluded_match:
        claim_id, status = concluded_match
        ref_no = f"#{claim_id}" if claim_id else "已有案件"
        reason = f"重复理赔：您本次申请的理赔已在{ref_no}做出赔付结论。根据一事不二理原则，本次重复申请不予赔付。"
        remark = f"航班延误: 拒绝。{reason}"
        return {
            **base_info, "forceid": forceid, "claim_type": "flight_delay",
            "Remark": remark, "IsAdditional": "N",
            "KeyConclusions": [{"checkpoint": "重复理赔检测", "Eligible": "N", "Remark": remark}],
            "flight_delay_audit": {
                "audit_result": "拒绝", "explanation": reason,
                "logic_check": {"exclusion_triggered": True, "duplicate_claim_triggered": True, "duplicate_ref_claim_id": claim_id},
            },
            "DebugInfo": {
                "debug": [], "flight_delay": None,
                "duplicate_check": {"triggered": True, "scenario": "concluded", "ref_claim_id": claim_id, "ref_status": str(status), "reason": reason},
            },
            "reason": reason,
        }

    if unconcluded_matches:
        claim_id, status = unconcluded_matches[0]
        ref_no = f"#{claim_id}" if claim_id else "已有案件"
        reason = f"重复理赔：您本次申请的理赔与{ref_no}为同一事件（审核中）。根据一事不二理原则，本次重复申请不予赔付。"
        remark = f"航班延误: 拒绝。{reason}"
        return {
            **base_info, "forceid": forceid, "claim_type": "flight_delay",
            "Remark": remark, "IsAdditional": "N",
            "KeyConclusions": [{"checkpoint": "重复理赔检测", "Eligible": "N", "Remark": remark}],
            "flight_delay_audit": {
                "audit_result": "拒绝", "explanation": reason,
                "logic_check": {"exclusion_triggered": True, "duplicate_claim_triggered": True, "duplicate_ref_claim_id": claim_id},
            },
            "DebugInfo": {
                "debug": [], "flight_delay": None,
                "duplicate_check": {"triggered": True, "scenario": "unconcluded", "ref_claim_id": claim_id, "ref_status": str(status), "reason": reason},
            },
            "reason": reason,
        }

    return None
