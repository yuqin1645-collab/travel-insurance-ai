"""
baggage_delay stages — 视觉识别结果合并到 AI 结构化抽取结果。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.vision_preprocessor import prepare_attachments_for_claim

from .utils import _parse_dt_flexible


def _detect_gds_close_receipt(
    claim_folder: Path,
    claim_info: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    代码级检测：GDS/航司系统结案记录截图。

    当 Vision 模型无法识别 GDS 结案截图时，用 Tesseract OCR 直接检测：
    - "CLOSED" / "结案" / "已关闭" 状态
    - 结案时间戳（如 "2026-02-08 03:50"）
    - 档案号、航班信息等

    返回: {"baggage_receipt_time": "YYYY-MM-DD HH:MM", "source": "gds_close", ...} 或 None
    """
    try:
        import pytesseract
        from PIL import Image
        from app.config import config

        tesseract_path = str(getattr(config, "TESSERACT_PATH", ""))
        if not Path(tesseract_path).exists():
            return None

        pytesseract.pytesseract.tesseract_cmd = tesseract_path
    except ImportError:
        return None

    # 获取所有附件 — GDS 检测优先使用原始图片（预处理会破坏档案号文字）
    try:
        # 先尝试从 claim_folder 直接读取原始图片
        original_files = list(claim_folder.glob("file_*.[jpJP][npPN]*"))
        if original_files:
            # 使用原始文件构建附件列表
            att_list = [type('Attachment', (), {'path': p}) for p in sorted(original_files)]
        else:
            # 回退到预处理的附件
            attachments, _ = prepare_attachments_for_claim(claim_folder, claim_info=claim_info, max_attachments=0)
            att_list = attachments
    except Exception:
        return None

    # GDS 结案关键词模式
    closed_patterns = [
        r'CLOSED',
        r'结案',
        r'已关闭',
    ]

    # 时间戳模式（匹配 "2026-02-08 03:50" 或 "2026-02-08 03:50:51"）
    timestamp_pattern = r'(\d{4}[-/]\d{2}[-/]\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)'

    # 档案号/参考号模式（GDS特征）- 放宽匹配，包含字母+数字组合
    archive_patterns = [
        r'[A-Z]{2,6}\d{4,10}',      # 标准格式: RSYC15746
        r'[A-Z]{3,8}\d{3,8}',        # 变体: AHLxxx, RSYCA15746
        r'AHL\s*[:：]?\s*[A-Z0-9]+', # AHL: RSYC15746
        r'卷宗.*[:：]?\s*[A-Z0-9]+', # 卷宗号
    ]

    for att in att_list:
        try:
            img = Image.open(att.path)
            text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        except Exception:
            continue

        # 检查是否有 CLOSED/结案关键词
        has_closed = any(re.search(p, text, re.IGNORECASE) for p in closed_patterns)
        if not has_closed:
            continue

        # 检查是否有时间戳
        time_matches = re.findall(timestamp_pattern, text)
        if not time_matches:
            continue

        # 检查是否有档案号（GDS特征）
        has_archive = any(re.search(pat, text) for pat in archive_patterns)

        if has_closed and time_matches and has_archive:
            # 找到最晚的时间戳作为结案时间
            latest_time = None
            latest_str = ""
            for date_str, time_str in time_matches:
                time_str = time_str.rstrip(':')
                full_str = f"{date_str} {time_str}"
                dt = _parse_dt_flexible(full_str)
                if dt and (latest_time is None or dt > latest_time):
                    latest_time = dt
                    latest_str = full_str

            if latest_time:
                # 验证结案时间晚于航班到达时间（合理）
                flight_arrival = None
                # 尝试从 claim_info 获取 Date_of_Accident
                doa = claim_info.get("Date_of_Accident", "")
                if doa:
                    flight_arrival = _parse_dt_flexible(doa)

                # 结案时间应该在事故日期之后
                if flight_arrival is None or latest_time >= flight_arrival:
                    return {
                        "baggage_receipt_time": latest_str,
                        "baggage_receipt_time_source": "gds_close",
                        "has_baggage_receipt_time_proof": True,
                        "source_file": att.path.name,
                        "confidence": "medium",
                        "notes": f"GDS结案记录检测：{att.path.name} 显示 CLOSED 状态，结案时间 {latest_str}",
                    }

    return None


async def _merge_vision_to_parsed(
    *,
    vision_extract: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    claim_info: Dict[str, Any],
    claim_folder: Path,
    debug: Dict[str, Any],
    reviewer: Any,
    session: aiohttp.ClientSession,
    text_blob: str = "",
) -> Dict[str, Any]:
    """将视觉识别结果合并到 AI 结构化抽取结果，含交叉校验、矛盾检测和 PIR 二次提取。"""

    # 合并视觉识别结果到 ai_parsed
    for key in (
        "has_boarding_or_ticket", "has_baggage_delay_proof", "has_baggage_receipt_time_proof",
        "has_baggage_tag_proof",
        "has_airline_baggage_record", "airline_baggage_record_name",
        "airline_baggage_record_flight", "airline_baggage_record_pieces",
        "flight_actual_arrival_time", "baggage_receipt_time", "baggage_receipt_time_source",
        "baggage_estimated_arrival_time",
        "receipt_times", "delay_hours",
        "has_id_proof", "has_passport", "has_exit_entry_record", "exit_datetime",
        "has_bank_card_proof", "risk_flags",
        "all_flights_found",
        "alternate",
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

    # 安全网0："次日/第二天"日期匹配修正
    # 当文本描述包含"第二天/次日/翌日/tomorrow/next day"等关键词，而提取的签收时间
    # 的日期与航班到达日期相同（或无明确日期）时，自动将签收时间日期+1天
    _fix_next_day_receipt_time(vision_extract, ai_parsed, text_blob, debug)

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

    # 安全网2：GDS/航司系统结案记录代码检测
    # 当 Vision 未提取到签收时间，或来源不可靠（pir_creation/email_estimate）时，用 OCR 直接检测 GDS 结案截图
    _current_receipt = ai_parsed.get("baggage_receipt_time") or ""
    _current_source = str(ai_parsed.get("baggage_receipt_time_source") or "").strip().lower()
    _unreliable_sources_gds = {"pir_creation", "email_estimate", "app_tracking", "email_notification", "transfer_flight_estimate"}
    _needs_gds_fallback = (
        (not _current_receipt or str(_current_receipt).lower() in ("unknown", ""))
        or _current_source in _unreliable_sources_gds
    )
    _gds_detected = False
    if _needs_gds_fallback:
        gds_result = _detect_gds_close_receipt(claim_folder, claim_info)
        if gds_result:
            ai_parsed["baggage_receipt_time"] = gds_result["baggage_receipt_time"]
            ai_parsed["baggage_receipt_time_source"] = gds_result["baggage_receipt_time_source"]
            ai_parsed["has_baggage_receipt_time_proof"] = gds_result["has_baggage_receipt_time_proof"]
            _gds_detected = True
            debug.setdefault("auto_corrected", []).append(
                f"baggage_receipt_time: GDS结案记录代码检测成功 {gds_result['baggage_receipt_time']}（文件: {gds_result['source_file']}）"
            )

    receipt_time = vision_extract.get("baggage_receipt_time") or ""
    if receipt_time and str(receipt_time).lower() not in ("unknown", ""):
        # 如果 GDS 检测已经成功，跳过 Vision 不可靠来源的清除逻辑
        if _gds_detected:
            debug.setdefault("auto_corrected", []).append(
                f"baggage_receipt_time: GDS检测已生效，跳过 Vision 不可靠来源清除"
            )
        else:
            low_confidence_markers = ["/unknown", "/未知", "~", "约", "左右", "estimated", "大概"]
            is_low_confidence = any(m in str(receipt_time) for m in low_confidence_markers)
            # 检查签收时间来源：邮件/APP估算、PIR创建时间等非实际签收来源不可靠
            _receipt_source = str(vision_extract.get("baggage_receipt_time_source") or ai_parsed.get("baggage_receipt_time_source") or "").strip().lower()
            _unreliable_sources = {"email_estimate", "app_estimate", "app_tracking", "pir_creation", "email_notification", "transfer_flight_estimate"}
            _is_unreliable_source = _receipt_source in _unreliable_sources
            # P1修复：GDS结案记录是有效间接证明，明确标记为可靠
            _is_gds_close = _receipt_source == "gds_close"
            if _is_gds_close:
                # GDS结案时间是有效间接证明，确保 has_baggage_receipt_time_proof = true
                hr_val = ai_parsed.get("has_baggage_receipt_time_proof")
                if not hr_val or str(hr_val).lower() != "true":
                    ai_parsed["has_baggage_receipt_time_proof"] = True
                    debug.setdefault("auto_corrected", []).append("has_baggage_receipt_time_proof: GDS结案记录是有效间接证明，已纠正为 true")
                debug.setdefault("auto_corrected", []).append(f"baggage_receipt_time: GDS结案时间 {_receipt_source} 作为有效间接证明接受")
            # 也检查 vision notes 是否表明时间来自邮件/转运航班预估
            _vision_notes = str(vision_extract.get("notes") or "").strip()
            _notes_indicate_email = any(kw in _vision_notes for kw in ["邮件", "邮件通知", "邮件预计", "转运航班", "行李将搭乘", "luggage will arrive", "baggage will arrive"])
            # 交叉校验：签收时间 = alternate航班起飞时间 且 alternate来源为航司邮件 → 非实际签收
            _alternate = ai_parsed.get("alternate") or vision_extract.get("alternate") or {}
            _alt_dep = str(_alternate.get("alt_dep") or "").strip()
            _alt_source = str(_alternate.get("alt_source") or "").strip().lower()
            _is_email_alt = any(kw in _alt_source for kw in ["邮件", "email", "通知", "航司邮件"])
            if _alt_dep and str(receipt_time).strip() == _alt_dep and _is_email_alt:
                ai_parsed["baggage_receipt_time"] = None
                ai_parsed["delay_hours"] = None
                ai_parsed["has_baggage_receipt_time_proof"] = False
                debug.setdefault("auto_corrected", []).append(
                    f"baggage_receipt_time: 签收时间 {receipt_time} = alternate航班起飞时间且来源为{_alternate.get('alt_source')}，"
                    f"判定为转运航班预估时间而非实际签收，清除"
                )
                return  # 已清除，不再继续auto-correct
            if not is_low_confidence and not _is_unreliable_source and not _notes_indicate_email:
                hr_val = ai_parsed.get("has_baggage_receipt_time_proof")
                if not hr_val or str(hr_val).lower() == "false":
                    ai_parsed["has_baggage_receipt_time_proof"] = True
                    debug.setdefault("auto_corrected", []).append("has_baggage_receipt_time_proof: 签收时间已提取但 vision 误判为 false，已自动纠正")
            else:
                _reason = []
                if is_low_confidence:
                    _reason.append(f"低置信度标记")
                if _is_unreliable_source:
                    _reason.append(f"来源{_receipt_source}不可靠")
                if _notes_indicate_email:
                    _reason.append("vision notes表明时间来自邮件/转运预估")
                ai_parsed["baggage_receipt_time"] = None
                ai_parsed["delay_hours"] = None
                ai_parsed["has_baggage_receipt_time_proof"] = False
                debug.setdefault("auto_corrected", []).append(f"baggage_receipt_time: 清除不可靠时间值 {receipt_time}（{'；'.join(_reason)}）")

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
    # P1 修复：但有"签收单"关键词时例外 — 不正常行李运输签收单是有效证明
    receipt_time_email_markers = [
        "航空公司邮件", "邮件通知", "邮件预计", "邮件预计",
        "转运航班", "行李搭乘", "预计.*到达", "行李将搭乘",
        "luggage will arrive", "baggage will arrive",
    ]
    # P1 修复：签收单关键词 — 这些是有效行李延误证明，不应被误判为邮件/转运航班
    _RECEIPT_DOC_KEYWORDS = ["签收单", "运输签收", "不正常行李", "行李事故记录", "PIR"]
    _proof_source = str(vision_extract.get("baggage_delay_proof_source") or "").strip()
    _has_receipt_doc = any(kw in _proof_source for kw in _RECEIPT_DOC_KEYWORDS)
    _notes_has_receipt_doc = any(kw in vision_notes for kw in _RECEIPT_DOC_KEYWORDS)

    if ai_parsed.get("has_baggage_receipt_time_proof") and vision_notes and not _has_receipt_doc and not _notes_has_receipt_doc:
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

    # 校验：无实际签收证明时，清除 baggage_receipt_time（防止PIR创建时间/航班到达时间被误用）
    receipt_proof_val = str(ai_parsed.get("has_baggage_receipt_time_proof") or "").strip().lower()
    receipt_time_val = ai_parsed.get("baggage_receipt_time") or ""
    receipt_source_val = str(ai_parsed.get("baggage_receipt_time_source") or "").strip().lower()
    if receipt_proof_val == "false" and receipt_time_val and str(receipt_time_val).lower() not in ("unknown", ""):
        # 没有签收证明但有签收时间，说明时间来源不可靠（PIR创建时间/航班到达时间等）
        ai_parsed["baggage_receipt_time"] = None
        ai_parsed["delay_hours"] = None
        debug.setdefault("auto_corrected", []).append(
            f"baggage_receipt_time: 无实际签收证明（has_baggage_receipt_time_proof=false），清除不可靠时间值 {receipt_time_val}"
        )
    elif receipt_source_val in ("pir_creation", "email_estimate", "app_tracking"):
        # 签收时间来源分类明确为不可靠来源，清除
        # P1修复：但如果 has_baggage_receipt_time_proof=true（说明 Vision 识别到了签收/交接单据），
        # 且时间是从文本描述中提取的（与交接单日期一致），则保留时间并修正来源
        _has_proof = ai_parsed.get("has_baggage_receipt_time_proof")
        _vision_notes = str(vision_extract.get("notes") or "").strip()
        _text_has_time = any(kw in _vision_notes for kw in ["文字描述", "文本描述", "description", "旅客"])
        if receipt_source_val == "pir_creation" and _has_proof and _text_has_time:
            # 文本描述提供了签收时间，且有交接单证明日期，接受该时间
            ai_parsed["baggage_receipt_time_source"] = "airport_counter"
            debug.setdefault("auto_corrected", []).append(
                f"baggage_receipt_time: 来源修正为 airport_counter（交接单存在但时间来自文本描述，日期一致）"
            )
        else:
            ai_parsed["baggage_receipt_time"] = None
            ai_parsed["delay_hours"] = None
            debug.setdefault("auto_corrected", []).append(
                f"baggage_receipt_time: 来源分类为 {receipt_source_val}（非实际签收），清除时间值 {receipt_time_val}"
            )
    elif receipt_time_val and str(receipt_time_val).lower() not in ("unknown", ""):
        # 额外检查：签收时间 = 航班到达时间 → 误将航班到达当成签收
        arrival_val = ai_parsed.get("flight_actual_arrival_time") or ""
        def _normalize_for_compare(s):
            """提取日期时间核心部分用于比较"""
            s = str(s).strip()
            # 移除时区、秒、分隔符
            s = s.split("+")[0].split("Z")[0].replace("T", " ").strip()
            # 移除秒
            parts = s.split(":")
            if len(parts) >= 3:
                s = ":".join(parts[:2])
            return s.replace(" ", "").replace("-", "").replace(":", "")

        if arrival_val and _normalize_for_compare(receipt_time_val) == _normalize_for_compare(arrival_val):
            ai_parsed["baggage_receipt_time"] = None
            ai_parsed["delay_hours"] = None
            ai_parsed["has_baggage_receipt_time_proof"] = False
            debug["no_receipt_proof_confirmed"] = True
            debug.setdefault("auto_corrected", []).append(
                f"baggage_receipt_time: 签收时间与航班到达时间完全相同（{receipt_time_val} vs {arrival_val}），"
                f"疑为误将航班到达当成行李签收，清除时间并标记无签收证明"
            )

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


def _fix_next_day_receipt_time(
    vision_extract: Dict[str, Any],
    ai_parsed: Dict[str, Any],
    text_blob: str,
    debug: Dict[str, Any],
) -> None:
    """修正"第二天/次日"场景下的签收时间日期匹配错误。

    当文本描述包含"第二天/次日/翌日"等关键词，而提取的签收时间日期与航班到达日期
    相同时，自动将签收时间日期+1天。
    """
    receipt_time = ai_parsed.get("baggage_receipt_time") or vision_extract.get("baggage_receipt_time")
    if not receipt_time:
        return

    # 检查文本中是否有"次日/第二天"等关键词
    next_day_markers = [
        "第二天", "次日", "翌日", "第二天的",
        "tomorrow", "next day", "the following day", "the next day",
        "隔天", "隔日", "过后", "才收到", "才送达",
    ]
    has_next_day = any(m in text_blob.lower() for m in next_day_markers)
    if not has_next_day:
        return

    # 尝试解析签收时间和航班到达时间
    receipt_dt = _parse_dt_flexible(receipt_time)
    arrival_raw = ai_parsed.get("flight_actual_arrival_time") or vision_extract.get("flight_actual_arrival_time")
    arrival_dt = _parse_dt_flexible(arrival_raw) if arrival_raw else None

    if not receipt_dt:
        return

    # 如果签收时间 <= 到达时间（不可能），说明日期配错了
    if arrival_dt and receipt_dt <= arrival_dt:
        from datetime import timedelta
        fixed_dt = receipt_dt + timedelta(days=1)
        ai_parsed["baggage_receipt_time"] = fixed_dt.strftime("%Y-%m-%d %H:%M")
        debug.setdefault("auto_corrected", []).append(
            f"baggage_receipt_time: 次日场景修正 {receipt_time} -> {fixed_dt.strftime('%Y-%m-%d %H:%M')}（签收时间≤到达时间，日期+1天）"
        )
    elif not arrival_dt:
        # 无到达时间参考，但文本明确说"第二天"，且签收时间的时间部分与PIR常见时间格式一致
        # 这种情况不做自动修正，只记录警告
        debug.setdefault("next_day_warning", []).append(
            f"baggage_receipt_time={receipt_time}，文本提及次日但无到达时间参考，未自动修正"
        )
