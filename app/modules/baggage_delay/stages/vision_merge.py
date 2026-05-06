"""
baggage_delay stages — 视觉识别结果合并到 AI 结构化抽取结果。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import aiohttp

from app.vision_preprocessor import prepare_attachments_for_claim


async def _merge_vision_to_parsed(
    *,
    vision_extract: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    claim_folder: Path,
    debug: Dict[str, Any],
    reviewer: Any,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """将视觉识别结果合并到 AI 结构化抽取结果，含交叉校验、矛盾检测和 PIR 二次提取。"""

    # 合并视觉识别结果到 ai_parsed
    for key in (
        "has_boarding_or_ticket", "has_baggage_delay_proof", "has_baggage_receipt_time_proof",
        "has_baggage_tag_proof",
        "has_airline_baggage_record", "airline_baggage_record_name",
        "airline_baggage_record_flight", "airline_baggage_record_pieces",
        "flight_actual_arrival_time", "baggage_receipt_time", "receipt_times", "delay_hours",
        "has_id_proof", "has_passport", "has_exit_entry_record", "exit_datetime",
        "has_bank_card_proof", "risk_flags",
        "all_flights_found",
    ):
        vision_val = vision_extract.get(key)
        parsed_val = ai_parsed.get(key)
        if vision_val is not None and str(vision_val).lower() not in ("unknown", "", "[]"):
            ai_parsed[key] = vision_val
        elif parsed_val is None:
            ai_parsed[key] = vision_val
    for key in ("flight_no", "flight_date", "dep_iata", "arr_iata"):
        vision_val = vision_extract.get(key)
        if vision_val and str(vision_val).lower() not in ("unknown", ""):
            existing = ai_parsed.get(key)
            if existing is None or str(existing).lower() in ("unknown", ""):
                ai_parsed[key] = vision_val

    # 安全网：交叉校验
    proof_source = vision_extract.get("baggage_delay_proof_source") or ""
    if proof_source and str(proof_source).lower() not in ("unknown", ""):
        hd_val = ai_parsed.get("has_baggage_delay_proof")
        if not hd_val or str(hd_val).lower() == "false":
            ai_parsed["has_baggage_delay_proof"] = True
            debug.setdefault("auto_corrected", []).append("has_baggage_delay_proof: PIR报告存在但 vision 误判为 false，已自动纠正")
        ht_val = ai_parsed.get("has_baggage_tag_proof")
        if not ht_val or str(ht_val).lower() == "false":
            ai_parsed["has_baggage_tag_proof"] = True
            debug.setdefault("auto_corrected", []).append("has_baggage_tag_proof: PIR报告含航班+行李信息，等效行李牌，已自动纠正")

    receipt_time = vision_extract.get("baggage_receipt_time") or ""
    if receipt_time and str(receipt_time).lower() not in ("unknown", ""):
        low_confidence_markers = ["/unknown", "/未知", "~", "约", "左右", "estimated", "大概"]
        is_low_confidence = any(m in str(receipt_time) for m in low_confidence_markers)
        if not is_low_confidence:
            hr_val = ai_parsed.get("has_baggage_receipt_time_proof")
            if not hr_val or str(hr_val).lower() == "false":
                ai_parsed["has_baggage_receipt_time_proof"] = True
                debug.setdefault("auto_corrected", []).append("has_baggage_receipt_time_proof: 签收时间已提取但 vision 误判为 false，已自动纠正")
        else:
            ai_parsed["baggage_receipt_time"] = None
            ai_parsed["delay_hours"] = None
            debug.setdefault("auto_corrected", []).append(f"baggage_receipt_time: 清除低置信度时间值 {receipt_time}")

    vision_notes = str(vision_extract.get("notes") or "").strip()

    # 校验：如果 vision notes 明确说行李延误证明缺失，纠正 has_baggage_delay_proof 为 false
    delay_proof_missing_markers = [
        "行李延误证明文件缺失", "行李延误证明缺失", "行李延误证明.*缺失",
        "未见行李延误证明", "无行李延误证明",
    ]
    for marker in delay_proof_missing_markers:
        if re.search(marker, vision_notes):
            if ai_parsed.get("has_baggage_delay_proof") not in (None, False):
                ai_parsed["has_baggage_delay_proof"] = False
                ai_parsed["delay_hours"] = None
                debug.setdefault("auto_corrected", []).append(
                    f"has_baggage_delay_proof: vision notes明确行李延误证明缺失，纠正为 false"
                )
            break

    # 校验：如果 vision notes 明确说明签收时间来自航空公司邮件通知/转运航班预计到达时间，
    # 说明并非真正的行李签收证明，应将 has_baggage_receipt_time_proof 纠正为 false
    receipt_time_email_markers = [
        "航空公司邮件", "邮件通知", "邮件预计", "邮件预计",
        "转运航班", "行李搭乘", "预计.*到达", "行李将搭乘",
        "luggage will arrive", "baggage will arrive",
    ]
    if ai_parsed.get("has_baggage_receipt_time_proof") and vision_notes:
        for marker in receipt_time_email_markers:
            if re.search(marker, vision_notes):
                ai_parsed["has_baggage_receipt_time_proof"] = False
                ai_parsed["baggage_receipt_time"] = None
                ai_parsed["delay_hours"] = None
                debug["no_receipt_proof_confirmed"] = True
                debug.setdefault("auto_corrected", []).append(
                    f"has_baggage_receipt_time_proof: vision notes明确时间来自邮件/转运航班，非实际签收证明，纠正为 false"
                )
                break

    # 校验：Vision 模型内部矛盾检测 —— document_sources 中标记为 absent 但顶层 flag 为 true
    doc_sources = vision_extract.get("document_sources") or {}
    if isinstance(doc_sources, dict):
        _source_flag_map = {
            "baggage_delay_proof": ("has_baggage_delay_proof", "行李延误证明"),
            "baggage_receipt_time_proof": ("has_baggage_receipt_time_proof", "行李签收时间证明"),
            "baggage_tag_proof": ("has_baggage_tag_proof", "托运行李牌"),
            "boarding_pass": ("has_boarding_or_ticket", "登机牌/机票"),
        }
        for source_key, (flag_key, label) in _source_flag_map.items():
            source_status = str(doc_sources.get(source_key, {}).get("status") or "").strip().lower()
            flag_val = str(ai_parsed.get(flag_key) or "").strip().lower()
            if source_status == "absent" and flag_val == "true":
                ai_parsed[flag_key] = False
                if flag_key in ("has_baggage_delay_proof", "has_baggage_receipt_time_proof"):
                    ai_parsed["delay_hours"] = None
                debug.setdefault("auto_corrected", []).append(
                    f"{flag_key}: document_sources.{source_key}=absent 与顶层 flag=true 矛盾，以 document_sources 为准纠正为 false"
                )

    # PIR二次聚焦提取
    needs_pir_extract = (
        ai_parsed.get("has_baggage_delay_proof") is True
        and not debug.get("no_receipt_proof_confirmed")
        and (not ai_parsed.get("baggage_receipt_time")
             or str(ai_parsed.get("baggage_receipt_time")).lower() in ("unknown", ""))
        and (not ai_parsed.get("delay_hours")
             or str(ai_parsed.get("delay_hours")).lower() in ("unknown", ""))
    )
    if needs_pir_extract:
        try:
            processed_attachments, _ = prepare_attachments_for_claim(
                claim_folder, claim_info=claim_info, max_attachments=0
            )
            attachment_paths = [a.path for a in processed_attachments]
            if not attachment_paths:
                debug["pir_receipt_extract"] = {"attempted": False, "reason": "无可用图片附件"}
            else:
                pir_extract = await reviewer._ai_pir_receipt_time_extract_async(
                    attachment_paths=attachment_paths,
                    claim_info=claim_info,
                    session=session,
                )
                if isinstance(pir_extract, dict):
                    receipt = pir_extract.get("baggage_receipt_time")
                    confidence = str(pir_extract.get("confidence") or "").lower()
                    if receipt and str(receipt).lower() not in ("unknown", "") and confidence in ("high", "medium"):
                        ai_parsed["baggage_receipt_time"] = receipt
                        if pir_extract.get("receipt_times"):
                            ai_parsed["receipt_times"] = pir_extract["receipt_times"]
                        pir_delay = pir_extract.get("delay_hours")
                        if pir_delay and str(pir_delay).lower() != "unknown":
                            ai_parsed["delay_hours"] = pir_delay
                        debug.setdefault("auto_corrected", []).append(
                            f"baggage_receipt_time: PIR二次提取成功 {receipt}（置信度: {confidence}）"
                        )
                    else:
                        debug["pir_receipt_extract"] = {
                            "attempted": True, "result": "未提取到有效签收时间",
                            "confidence": confidence,
                        }
        except Exception as e:
            debug["pir_receipt_extract_warning"] = str(e)[:200]

    return ai_parsed
