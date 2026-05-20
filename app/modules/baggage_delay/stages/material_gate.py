"""
baggage_delay stages — 材料门禁校验。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .handlers import _check_airline_baggage_record_exception, _check_special_materials, _material_gate


def _has_flag(ai_parsed: Dict[str, Any], key: str) -> str:
    return str(ai_parsed.get(key) or "unknown").strip().lower()


def run_material_gate(
    ai_parsed: Dict[str, Any],
    vision_extract: Dict[str, Any],
    claim_info: Dict[str, Any],
    text_blob: str,
    file_names: List[str],
    debug: Dict[str, Any],
) -> Optional[List[str]]:
    """材料门禁：检查必备材料是否齐全。

    返回 None 表示全部通过；返回 list 表示缺失材料列表（应触发补件）。
    """
    missing_materials: List[str] = []

    if not isinstance(ai_parsed, dict):
        missing_materials = _material_gate(text_blob, file_names)
        debug["missing_materials"] = missing_materials
        return missing_materials if missing_materials else None

    flag = _has_flag(ai_parsed, "has_boarding_or_ticket")
    joined_text = f"{text_blob} {' '.join(file_names)}".lower()
    boarding_kw = ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"]

    if flag == "false":
        if not any(w in joined_text for w in boarding_kw):
            missing_materials.append("交通票据（机票/登机牌/行程单）")
    elif flag == "unknown":
        if not any(w in joined_text for w in boarding_kw):
            missing_materials.append("交通票据（机票/登机牌/行程单）")

    delay_proof_flag = _has_flag(ai_parsed, "has_baggage_delay_proof")
    receipt_proof_flag = _has_flag(ai_parsed, "has_baggage_receipt_time_proof")
    delay_proof_kw = any(w in joined_text for w in ["行李延误", "行李不正常", "行李事故", "行李未到", "pir", "baggage delay",
                         "delay proof", "property irregularity", "lost baggage", "baggage claim",
                         "worldtracer", "world tracer"])
    receipt_proof_kw = any(w in joined_text for w in ["签收", "领取", "提取", "取件", "派送", "送达", "交付",
                           "行李到达", "行李已", "receipt", "delivered", "delivery", "received",
                           "collected", "picked up", "acknowledgement", "acknowledgment"])

    has_delay_proof = delay_proof_flag == "true" or (delay_proof_flag != "true" and delay_proof_kw)
    has_receipt_proof = receipt_proof_flag == "true" or (receipt_proof_flag != "true" and receipt_proof_kw)

    if not has_delay_proof and not has_receipt_proof:
        missing_materials.append("行李延误证明或行李签收单（航空公司出具的行李延误时数/原因书面证明，或含具体签收时间的行李签收单，二选一）")

    boarding_flag = _has_flag(ai_parsed, "has_boarding_or_ticket")
    tag_flag = _has_flag(ai_parsed, "has_baggage_tag_proof")
    id_flag = _has_flag(ai_parsed, "has_id_proof")
    passport_flag = _has_flag(ai_parsed, "has_passport")

    has_boarding = boarding_flag == "true" or any(w in joined_text for w in boarding_kw)

    has_baggage_tag = tag_flag == "true"
    if tag_flag in ("false", "unknown"):
        if tag_flag == "unknown":
            v_tag = str(vision_extract.get("has_baggage_tag_proof") or "unknown").strip().lower()
            if v_tag not in ("unknown", ""):
                has_baggage_tag = v_tag == "true"
        if not has_baggage_tag:
            exception_met = _check_airline_baggage_record_exception(
                vision_extract, ai_parsed or {}, claim_info, joined_text
            )
            if exception_met:
                has_baggage_tag = True
                debug["baggage_tag_exception"] = "航空公司官方行李记录满足替代条件，视同行李牌已提供"

    has_id = id_flag == "true" or passport_flag == "true"

    key_materials_confirmed = has_delay_proof and has_boarding and has_baggage_tag and has_id
    if key_materials_confirmed and not has_receipt_proof:
        debug["receipt_proof_relaxed"] = (
            "行李延误证明+登机牌+行李牌+身份证已确认，签收证明缺失不阻断，放行到时长计算阶段"
        )

    if not has_baggage_tag:
        missing_materials.append("托运行李牌照片（含姓名、航班信息、行李牌号码）")

    if id_flag == "false" and passport_flag == "false":
        missing_materials.append("被保险人身份证正反面或护照")
    if passport_flag == "false" and id_flag in ("false", "unknown"):
        missing_materials.append("护照照片页、签证页、出入境盖章页")

    bank_flag = _has_flag(ai_parsed, "has_bank_card_proof")
    if bank_flag == "false":
        debug["bank_card_warning"] = "视觉识别未见银行卡信息，建议人工确认打款账号"

    special_needs = _check_special_materials(claim_info, text_blob, file_names)
    missing_materials.extend(special_needs)
    missing_materials = sorted(set(missing_materials))

    debug["missing_materials"] = missing_materials
    return missing_materials if missing_materials else None