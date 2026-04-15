"""
Skill H/E/F/G/I: 保单/客票/材料相关 Skills
- Skill E: policy.lookup_effective_window（保单权益窗口）
- Skill F: booking.lookup_ticket_status（客票号使用状态）
- Skill G: flight.lookup_rebooking（改签记录查询）
- Skill H: policy.lookup_coverage_area（保单承保区域）
- Skill I: material.verify_evidence（材料真实性校验）

注意：大部分 Skills 依赖后端接口，此处提供接口规范 + 代码侧硬校验逻辑。
无后端接口时降级为"补材/转人工"，不硬拒赔。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.logging_utils import LOGGER


# ==================== Skill E: policy.lookup_effective_window ====================

def lookup_effective_window(claim_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Skill E: 从 claim_info 中提取并校验保单权益有效期窗口。
    优先从结构化字段读取，缺失则返回 unknown。

    Returns:
        {
            "effective_from": str,  # 保单生效日 YYYY-MM-DD
            "effective_to": str,    # 保单满期日 YYYY-MM-DD
            "is_allianz": bool,     # 是否安联保险（影响顺延规则）
            "coverage_status": "valid" | "expired" | "not_started" | "unknown",
            "note": str,
        }
    """
    # 从 claim_info 常见字段提取（优先精确到秒的字段）
    effective_from_raw = (
        claim_info.get("Effective_Date")
        or claim_info.get("Insurance_Period_From")
        or claim_info.get("effective_from")
        or claim_info.get("Policy_Start_Date")
        or ""
    )
    effective_to_raw = (
        claim_info.get("Expiry_Date")
        or claim_info.get("Insurance_Period_To")
        or claim_info.get("effective_to")
        or claim_info.get("Policy_End_Date")
        or ""
    )
    insurer = str(claim_info.get("Insurer") or claim_info.get("insurer") or claim_info.get("Insurance_Company") or "")
    is_allianz = "安联" in insurer or "allianz" in insurer.lower()

    ef = _parse_datetime_str(str(effective_from_raw).strip())
    et = _parse_datetime_str(str(effective_to_raw).strip())
    # 降级：若 datetime 解析失败，尝试纯日期（生效日取00:00，满期日取23:59:59）
    if ef is None:
        _ef_date = _parse_date_str(str(effective_from_raw).strip())
        if _ef_date:
            ef = datetime(_ef_date.year, _ef_date.month, _ef_date.day, 0, 0, 0)
    if et is None:
        _et_date = _parse_date_str(str(effective_to_raw).strip())
        if _et_date:
            et = datetime(_et_date.year, _et_date.month, _et_date.day, 23, 59, 59)

    if ef is None or et is None:
        return {
            "effective_from": str(effective_from_raw) or "unknown",
            "effective_to": str(effective_to_raw) or "unknown",
            "is_allianz": is_allianz,
            "coverage_status": "unknown",
            "note": "保单起止日期缺失，无法判定有效期，建议人工复核",
        }

    today = datetime.now()
    if today < ef:
        status = "not_started"
    elif today > et:
        status = "expired"
    else:
        status = "valid"

    return {
        "effective_from": ef.isoformat(),
        "effective_to": et.isoformat(),
        "is_allianz": is_allianz,
        "coverage_status": status,
        "note": f"保单有效期 {ef} ~ {et}，当前状态: {status}",
    }


def check_delay_in_coverage(
    delay_time: Optional[str],
    effective_from: Optional[str],
    effective_to: Optional[str],
    is_allianz: bool = False,
    first_exit_date: Optional[str] = None,
    time_basis_label: str = "延误相关时间",
) -> Dict[str, Any]:
    """
    校验延误发生时间是否在保单有效期内（含安联顺延规则）。
    使用 datetime 精度比较，保单起止如含时间点则精确到秒。

    Args:
        delay_time: 用于判断的时间点（如投保时间、出境时间、计划起飞时间等）
        effective_from: 保单生效时间
        effective_to: 保单到期时间
        is_allianz: 是否为安联保单
        first_exit_date: 首次出境时间（用于安联顺延规则）
        time_basis_label: 时间基准的名称，用于 note 中显示（如"投保时间"、"出境时间"等）

    安联顺延规则：
    - 若实际出境时间相比原保单生效日推迟不超过15日
    - 则生效日顺延为第一次出境日，满期日等期限顺延
    - 超过15日则按原有效期

    Returns:
        {
            "in_coverage": bool | None,
            "applied_from": str,
            "applied_to": str,
            "used_extension": bool,
            "note": str,
        }
    """
    # 优先用 datetime 精度解析保单起止（含时间点）
    ef_dt = _parse_datetime_str(effective_from)
    et_dt = _parse_datetime_str(effective_to)
    # 降级：仅有日期时，生效日取当天00:00，满期日取当天23:59:59
    ef_date = _parse_date_str(effective_from)
    et_date = _parse_date_str(effective_to)
    if ef_dt is None and ef_date is not None:
        ef_dt = datetime(ef_date.year, ef_date.month, ef_date.day, 0, 0, 0)
    if et_dt is None and et_date is not None:
        et_dt = datetime(et_date.year, et_date.month, et_date.day, 23, 59, 59)

    # 延误时间（datetime 精度解析）
    # 若输入仅含日期（无 HH:MM 部分），取当天 23:59:59（保守原则：时间未知按最晚判定，避免漏拒）
    _delay_has_time = bool(delay_time) and len(str(delay_time).strip()) > 10
    delay_dt = _parse_datetime_str(delay_time) if _delay_has_time else None
    if delay_dt is None:
        delay_date_only = _parse_date_str(delay_time)
        if delay_date_only is not None:
            delay_dt = datetime(delay_date_only.year, delay_date_only.month, delay_date_only.day, 23, 59, 59)

    if ef_dt is None or et_dt is None:
        return {
            "in_coverage": None,
            "applied_from": effective_from or "unknown",
            "applied_to": effective_to or "unknown",
            "used_extension": False,
            "note": "保单起止日期缺失，无法判定，需补材/人工复核",
        }
    if delay_dt is None:
        return {
            "in_coverage": None,
            "applied_from": ef_dt.isoformat(),
            "applied_to": et_dt.isoformat(),
            "used_extension": False,
            "note": "延误发生时间缺失，无法判定，需补材/人工复核",
        }

    applied_from_dt = ef_dt
    applied_to_dt = et_dt
    used_extension = False
    extension_type = ""  # "顺延" 或 "提前"

    # 安联顺延/提前生效逻辑（顺延以天为单位，生效/满期各加相同天数）
    # 场景1：出境时间晚于生效日（推迟）：保单生效日和满期日都顺延
    # 场景2：出境时间早于生效日（提前出境）：保单生效日和满期日都提前，但保障期限保持不变
    if is_allianz and first_exit_date:
        fex = _parse_date_str(first_exit_date)
        ef_date_only = ef_dt.date()
        if fex:
            diff_days = (fex - ef_date_only).days
            # 仅在出境时间与生效日差距在 ±15 天内时允许调整
            if abs(diff_days) <= 15:
                applied_from_dt = applied_from_dt + timedelta(days=diff_days)
                # 满期日也相应调整，确保保障期限长度不变
                applied_to_dt = applied_to_dt + timedelta(days=diff_days)
                used_extension = True
                extension_type = "顺延" if diff_days > 0 else "提前"

    in_coverage = applied_from_dt <= delay_dt <= applied_to_dt

    return {
        "in_coverage": in_coverage,
        "applied_from": applied_from_dt.isoformat(),
        "applied_to": applied_to_dt.isoformat(),
        "used_extension": used_extension,
        "note": (
            f"{time_basis_label} {delay_dt.strftime('%Y-%m-%d %H:%M')} "
            f"{'在' if in_coverage else '不在'}保单有效期 "
            f"{applied_from_dt.strftime('%Y-%m-%d %H:%M')}~{applied_to_dt.strftime('%Y-%m-%d %H:%M')} 内"
            + (f"（已应用安联{extension_type}规则，出境时间与原生效日相差{abs(diff_days)}天）" if used_extension else "")
        ),
    }


# ==================== Skill F: booking.lookup_ticket_status ====================

def lookup_ticket_status_stub(ticket_no: Optional[str]) -> Dict[str, Any]:
    """
    Skill F: 客票号使用状态（存根实现，待接入真实客票查询接口）。
    目前无后端接口，返回 unknown 并建议补材。
    """
    if not ticket_no:
        return {
            "ticket_no": None,
            "status": "unknown",
            "note": "未提供客票号，无法查询，建议补充客票号/登机牌",
        }
    return {
        "ticket_no": ticket_no,
        "status": "unknown",
        "note": f"客票号 {ticket_no} 查询接口尚未接入，建议人工核验票号使用状态",
    }


# ==================== Skill G: flight.lookup_rebooking ====================

def lookup_rebooking_stub(
    original_flight_no: Optional[str],
    flight_date: Optional[str],
    passenger_name: Optional[str],
) -> Dict[str, Any]:
    """
    Skill G: 改签记录查询（存根实现，待接入航司/GDS接口）。
    目前无后端接口，返回 unknown 并建议补材。
    """
    return {
        "original_flight_no": original_flight_no or "unknown",
        "flight_date": flight_date or "unknown",
        "rebooking_records": [],
        "status": "unknown",
        "note": "改签记录查询接口尚未接入，建议提供承运人改签记录证明或登机牌",
    }


# ==================== Skill H: policy.lookup_coverage_area ====================

def lookup_coverage_area(claim_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Skill H: 从 claim_info 中提取保单承保区域。
    优先从结构化字段读取，缺失则返回全球兜底（并建议人工确认）。

    Returns:
        {
            "region_type": "global" | "regional" | "single_country" | "unknown",
            "coverage_regions": [...],
            "note": str,
        }
    """
    region_raw = (
        claim_info.get("Coverage_Area")
        or claim_info.get("coverage_area")
        or claim_info.get("BenefitName")
        or ""
    )
    region_str = str(region_raw).strip()

    # 简单规则：包含"全球"/global 则全球
    if any(kw in region_str.lower() for kw in ["全球", "global", "worldwide"]):
        return {
            "region_type": "global",
            "coverage_regions": ["全球"],
            "note": "保单承保区域：全球",
        }

    if region_str:
        return {
            "region_type": "regional",
            "coverage_regions": [region_str],
            "note": f"保单承保区域：{region_str}（建议人工确认是否覆盖延误发生地）",
        }

    return {
        "region_type": "unknown",
        "coverage_regions": [],
        "note": "保单承保区域未知，建议人工确认",
    }


def check_delay_in_coverage_area(
    delay_iata: Optional[str],
    coverage_area: Dict[str, Any],
) -> Dict[str, Any]:
    """
    校验延误发生地是否在保单承保区域内。

    Returns:
        {
            "in_coverage": bool | None,
            "delay_iata": str,
            "region_type": str,
            "note": str,
        }
    """
    region_type = coverage_area.get("region_type", "unknown")

    if region_type == "global":
        return {
            "in_coverage": True,
            "delay_iata": delay_iata or "unknown",
            "region_type": region_type,
            "note": "保单为全球覆盖，延误发生地在承保范围内",
        }

    if region_type == "unknown" or not delay_iata:
        return {
            "in_coverage": None,
            "delay_iata": delay_iata or "unknown",
            "region_type": region_type,
            "note": "无法确认延误发生地是否在承保区域内，建议人工复核",
        }

    # 简单文本匹配（正式接入后替换为结构化比对）
    regions = coverage_area.get("coverage_regions") or []
    iata_upper = str(delay_iata).strip().upper()
    for r in regions:
        if iata_upper in str(r).upper():
            return {
                "in_coverage": True,
                "delay_iata": iata_upper,
                "region_type": region_type,
                "note": f"延误发生地 {iata_upper} 在承保区域 {r} 内",
            }

    # 已知承保区域类型但没有匹配到：
    # - 若承保区域字段是“可直接比对的代码”（国家2位/机场3位），则明确判定不在承保区域
    # - 若承保区域是“模糊描述”（如 欧洲/亚洲 等），没有可确定的映射关系，退回 unknown 交给人工
    if region_type != "unknown" and regions:
        def _looks_code(x: Any) -> bool:
            t = str(x or "").strip().upper()
            return (len(t) == 2 and t.isalpha()) or (len(t) == 3 and t.isalpha())

        if all(_looks_code(x) for x in regions):
            return {
                "in_coverage": False,
                "delay_iata": iata_upper,
                "region_type": region_type,
                "note": f"延误发生地 {iata_upper} 不在承保区域 {regions} 内",
            }

    # 无法确认：承保区域信息缺失或无法解析
    return {
        "in_coverage": None,
        "delay_iata": iata_upper,
        "region_type": region_type,
        "note": f"无法确认 {iata_upper} 是否在承保区域 {regions} 内，建议人工复核",
    }


# ==================== Skill I: material.verify_evidence ====================

def verify_evidence_basic(
    parsed_fields: Dict[str, Any],
    authoritative_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Skill I: 材料真实性基础校验（代码侧）
    比对模型从材料中抽取的字段 vs 权威航班数据。

    Args:
        parsed_fields: 模型抽取的字段（来自 stage1 解析结果）
        authoritative_data: 权威数据（来自 Skill A flight.lookup_status）

    Returns:
        {
            "is_consistent": bool | None,
            "discrepancies": [...],
            "risk_level": "low" | "medium" | "high" | "unknown",
            "note": str,
        }
    """
    if not authoritative_data or not authoritative_data.get("success"):
        return {
            "is_consistent": None,
            "discrepancies": [],
            "risk_level": "unknown",
            "note": "权威航班数据不可用，无法进行材料真实性比对",
        }

    discrepancies: List[str] = []

    # 比对航班号
    parsed_flight = _normalize_flight_no(
        str(parsed_fields.get("flight", {}).get("flight_no") or "")
    )
    auth_flight = _normalize_flight_no(
        str(authoritative_data.get("flight_no") or "")
    )
    if parsed_flight and auth_flight and parsed_flight != auth_flight:
        discrepancies.append(f"航班号不一致: 材料={parsed_flight}, 官方={auth_flight}")

    # 比对计划起飞时间（宽松：允许15分钟误差）
    parsed_dep = parsed_fields.get("utc", {}).get("planned_dep_utc") or ""
    auth_dep = authoritative_data.get("planned_dep") or ""
    if parsed_dep and auth_dep:
        diff_min = _time_diff_minutes(parsed_dep, auth_dep)
        if diff_min is not None and abs(diff_min) > 15:
            discrepancies.append(
                f"计划起飞时间偏差 {diff_min} 分钟: 材料={parsed_dep}, 官方={auth_dep}"
            )

    # 比对延误原因（有则比对）
    parsed_reason = str(parsed_fields.get("evidence", {}).get("delay_reason") or "")
    auth_reason = str(authoritative_data.get("delay_reason") or "")
    if parsed_reason and auth_reason:
        if not _reason_compatible(parsed_reason, auth_reason):
            discrepancies.append(f"延误原因不匹配: 材料={parsed_reason}, 官方={auth_reason}")

    risk_level = "low"
    if len(discrepancies) >= 2:
        risk_level = "high"
    elif len(discrepancies) == 1:
        risk_level = "medium"

    return {
        "is_consistent": len(discrepancies) == 0,
        "discrepancies": discrepancies,
        "risk_level": risk_level,
        "note": (
            f"发现{len(discrepancies)}处不一致，风险等级:{risk_level}，建议人工复核"
            if discrepancies
            else "材料与官方数据一致，无明显欺诈风险"
        ),
    }


# ==================== 内部工具 ====================

def _parse_date_str(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except Exception:
            continue
    return None


def _parse_datetime_str(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    # 各格式对应的目标字符串长度（而非 fmt 字符串本身的长度）
    _FORMATS = [
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y%m%d%H%M%S", 14),   # 20240211160000
        ("%Y-%m-%d", 10),
        ("%Y%m%d", 8),
    ]
    for fmt, slen in _FORMATS:
        try:
            return datetime.strptime(s[:slen], fmt)
        except Exception:
            continue
    return None


def _normalize_flight_no(s: str) -> str:
    return re.sub(r"\s+", "", s.upper().strip())


def _time_diff_minutes(t1: str, t2: str) -> Optional[int]:
    """计算两个时间字符串的差值（分钟），t2-t1"""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            d1 = datetime.strptime(t1[:len(fmt)], fmt)
            d2 = datetime.strptime(t2[:len(fmt)], fmt)
            return int((d2 - d1).total_seconds() // 60)
        except Exception:
            continue
    return None


def _reason_compatible(r1: str, r2: str) -> bool:
    """简单判断延误原因是否相容（不严格匹配，避免误判）"""
    kw_map = {
        "天气": ["weather", "wind", "rain", "snow", "fog", "storm", "typhoon", "weather"],
        "机械": ["mechanical", "technical", "aircraft"],
        "管制": ["atc", "air traffic", "control", "restriction"],
        "罢工": ["strike", "industrial"],
        "超售": ["overbooking", "oversell"],
    }
    r1l, r2l = r1.lower(), r2.lower()
    for kw_cn, kw_en_list in kw_map.items():
        in_r1 = kw_cn in r1l or any(kw in r1l for kw in kw_en_list)
        in_r2 = kw_cn in r2l or any(kw in r2l for kw in kw_en_list)
        if in_r1 and in_r2:
            return True
        if in_r1 != in_r2:
            # 一方有该类关键词，另一方完全没有 -> 不相容
            return False
    return True  # 无法判断 -> 宽松兜底
