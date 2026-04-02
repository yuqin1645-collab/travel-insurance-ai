from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import config


@dataclass(frozen=True)
class PrecheckResult:
    early_return: Optional[Dict[str, Any]]
    claim_amount: float
    remaining_coverage: float
    insured_amount: float


def run_precheck(
    *,
    claim_info: Dict[str, Any],
    forceid: str,
    ctx: Dict[str, Any],
) -> PrecheckResult:
    """
    快速预检查（不调用 AI）：
    - 保单有效期：不在有效期内直接拒赔
    - 重复理赔：已有终结结果(IsAdditional=N)则跳过
    - 同时返回常用的金额字段（供后续核算阶段使用）
    """

    # 1) 日期有效期
    try:
        accident_date = datetime.strptime(claim_info.get("Date_of_Accident", ""), "%Y-%m-%d")
        effective_date = datetime.strptime(claim_info.get("Effective_Date", ""), "%Y%m%d%H%M%S")
        expiry_date = datetime.strptime(claim_info.get("Expiry_Date", ""), "%Y%m%d%H%M%S")
        if not (effective_date <= accident_date <= expiry_date):
            return PrecheckResult(
                early_return={
                    "forceid": forceid,
                    "Remark": (
                        f"拒赔: 出险日期({accident_date.strftime('%Y-%m-%d')})"
                        f"不在保单有效期内({effective_date.strftime('%Y-%m-%d')}至{expiry_date.strftime('%Y-%m-%d')})"
                    ),
                    "IsAdditional": "N",
                    "KeyConclusions": [
                        {
                            "checkpoint": "保单有效期核对",
                            "Eligible": "N",
                            "Remark": f"出险日期{accident_date.strftime('%Y-%m-%d')}不在保单有效期内",
                        }
                    ],
                    "DebugInfo": ctx,
                },
                claim_amount=float(claim_info.get("Amount", 0) or 0),
                remaining_coverage=float(claim_info.get("Remaining_Coverage", 0) or 0),
                insured_amount=float(claim_info.get("Insured_Amount", 0) or 0),
            )
    except Exception:
        # 日期解析失败不做硬拒赔，交由后续阶段/人工核对
        pass

    # 2) 金额字段
    claim_amount = float(claim_info.get("Amount", 0) or 0)
    remaining_coverage = float(claim_info.get("Remaining_Coverage", 0) or 0)
    insured_amount = float(claim_info.get("Insured_Amount", 0) or 0)

    # 3) 重复理赔（已有终结结果则跳过）
    try:
        claim_type = str(claim_info.get("claim_type") or claim_info.get("claimType") or "baggage_damage")
        ns_file = config.REVIEW_RESULTS_DIR / claim_type / f"{forceid}_ai_review.json"
        flat_file = config.REVIEW_RESULTS_DIR / f"{forceid}_ai_review.json"
        result_file = ns_file if ns_file.exists() else flat_file
        if result_file.exists():
            existing = json.loads(result_file.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("IsAdditional") == "N":
                return PrecheckResult(
                    early_return={
                        "forceid": forceid,
                        "Remark": "该案件已审核过，请勿重复提交",
                        "IsAdditional": "N",
                        "KeyConclusions": [
                            {
                                "checkpoint": "重复理赔核对",
                                "Eligible": "N",
                                "Remark": "该保单已有审核结果，不可重复理赔",
                            }
                        ],
                        "DebugInfo": ctx,
                    },
                    claim_amount=claim_amount,
                    remaining_coverage=remaining_coverage,
                    insured_amount=insured_amount,
                )
    except Exception:
        pass

    return PrecheckResult(
        early_return=None,
        claim_amount=claim_amount,
        remaining_coverage=remaining_coverage,
        insured_amount=insured_amount,
    )

