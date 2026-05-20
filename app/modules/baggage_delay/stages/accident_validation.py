"""
baggage_delay stages — 事故类型校验（行李丢失 vs 行李延误）。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .utils import _parse_dt_flexible
from .calculator import _compute_delay_hours_by_rule


BAGGAGE_DELAY_THRESHOLD_HOURS = 6

_FOUND_KEYWORDS = ["找到", "送达", "领取", "取回", "收到", "delivered", "found", "recovered", "retrieved"]


def validate_accident_type(
    ai_parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    text_blob: str,
    conclusions: List[Dict[str, str]],
    debug: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """事故类型校验：行李丢失 vs 行李延误。

    返回 None 表示放行继续；返回 dict 表示应作为最终结果直接返回。
    """
    parsed_accident_type = str((ai_parsed or {}).get("accident_type") or "").strip().lower()
    raw_receipt = (ai_parsed or {}).get("baggage_receipt_time")
    has_receipt_time = bool(raw_receipt) and _parse_dt_flexible(str(raw_receipt)) is not None

    _receipt_times_list = (ai_parsed or {}).get("receipt_times") or []
    _has_any_receipt_time = has_receipt_time or any(
        _parse_dt_flexible(str(rt)) is not None
        for rt in _receipt_times_list
        if rt and str(rt).lower() not in ("unknown", "")
    )

    _has_delay_proof = str((ai_parsed or {}).get("has_baggage_delay_proof") or "").strip().lower() == "true"
    _has_delay_proof_source = str((vision_extract or {}).get("baggage_delay_proof_source") or "").strip()
    _has_receipt_doc = any(kw in _has_delay_proof_source for kw in ["签收单", "不正常行李", "运输签收", "行李事故记录"])

    delay_calc_temp = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
    has_calculable_delay = delay_calc_temp.get("delay_hours") is not None

    # 行李丢失误判修复
    if parsed_accident_type == "baggage_loss" and has_receipt_time:
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = "行李曾报丢失但已找回/送达，按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "行李曾报丢失但已找回/送达，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and has_calculable_delay:
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = "可计算延误时长，按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "可计算延误时长，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and _has_any_receipt_time:
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = "receipt_times列表中有签收时间，行李已找回，按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "签收时间列表中有有效时间，行李已找回，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and (_has_delay_proof or _has_receipt_doc):
        if isinstance(ai_parsed, dict):
            ai_parsed["accident_type"] = "baggage_delay"
            ai_parsed["accident_type_note"] = f"有行李延误证明（{_has_delay_proof_source}），按行李延误审核"
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": f"有{_has_delay_proof_source}，按行李延误审核"})
    elif parsed_accident_type == "baggage_loss" and not has_receipt_time and not has_calculable_delay:
        text_has_found = any(kw in text_blob.lower() for kw in _FOUND_KEYWORDS)
        if text_has_found:
            if isinstance(ai_parsed, dict):
                ai_parsed["accident_type"] = "baggage_delay"
                ai_parsed["accident_type_note"] = "文本提及行李已找到/送达，按行李延误审核"
            conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "文本提及行李已找到/送达，按行李延误审核"})
        else:
            conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
            return {
                "early_return": True,
                "remark": "拒赔：事故类型为行李丢失，非托运行李延误责任",
            }
    elif ("行李丢失" in text_blob) and ("延误" not in text_blob) and not _has_any_receipt_time and not has_calculable_delay:
        text_has_found = any(kw in text_blob.lower() for kw in ["找到", "送达", "领取", "取回", "收到", "delivered", "found", "recovered"])
        if text_has_found:
            conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "文本提及行李已找到/送达，按行李延误审核"})
        else:
            conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
            return {
                "early_return": True,
                "remark": "拒赔：事故类型为行李丢失，非托运行李延误责任",
            }
    else:
        conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "未发现行李丢失单独触发，继续按行李延误审核"})

    return None