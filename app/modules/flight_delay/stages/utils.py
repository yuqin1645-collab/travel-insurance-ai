"""
flight_delay stages — 纯工具函数（parsing、merging、formatting）。
从 pipeline.py 提取，保持原逻辑不变。
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.logging_utils import LOGGER, log_extra


def _policy_excerpt_or_default(claim_info: Dict[str, Any], policy_terms: str) -> str:
    if policy_terms:
        return policy_terms[:4000]
    insured_amount = str(claim_info.get("Insured_Amount") or claim_info.get("insured_amount") or "")
    return (
        "【缺少条款全文，按默认兜底】\n"
        "- 起赔标准：每满5小时赔付300元\n"
        "- 最高保额：1200元\n"
        f"- 赔付限额：{insured_amount or '以保单为准'}\n"
    )


def _is_unknown(v: Any) -> bool:
    """判断字段值是否为"未知"（null/unknown/空字符串，或含unknown后缀如JOG/unknown）。"""
    if v is None:
        return True
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("", "unknown", "null", "none"):
            return True
        if s.endswith("/unknown") or s.endswith("/null") or s.endswith("/none"):
            return True
    return False


def _merge_vision_into_parsed(parsed: Dict[str, Any], vision: Dict[str, Any]) -> Dict[str, Any]:
    """
    将视觉（Vision）抽取结果合并到文本解析结果中。
    Vision 只**填补** parsed 中的 unknown/null 字段，不覆盖已有非 unknown 值。
    """
    def _merge_dict(base: Any, override: Any) -> Any:
        if not isinstance(base, dict) or not isinstance(override, dict):
            return base
        merged = dict(base)
        for k, v_over in override.items():
            v_base = merged.get(k)
            if isinstance(v_base, dict) and isinstance(v_over, dict):
                merged[k] = _merge_dict(v_base, v_over)
            elif _is_unknown(v_base) and not _is_unknown(v_over):
                merged[k] = v_over
        return merged

    return _merge_dict(parsed, vision)


def _merge_aviation_into_parsed(parsed: Dict[str, Any], aviation: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 AviationStack 查询结果合并到 parsed 中。
    """
    if not aviation.get("success"):
        return parsed

    p = copy.deepcopy(parsed)

    def _fill(keys: list, value: Any):
        """按路径填入，只填 unknown/null"""
        node = p
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        last = keys[-1]
        if _is_unknown(node.get(last)) and not _is_unknown(value):
            node[last] = value

    has_revision_chain = isinstance(
        (parsed or {}).get("schedule_revision_chain"), list
    ) and len((parsed or {}).get("schedule_revision_chain", [])) > 0

    def _force_fill(keys: list, value: Any):
        """强制填入，只要飞常准有值就覆盖"""
        if _is_unknown(value):
            return
        node = p
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    if has_revision_chain:
        _fill(["aviation_scheduled", "planned_dep"], aviation.get("planned_dep"))
        _fill(["aviation_scheduled", "planned_arr"], aviation.get("planned_arr"))
        _fill(["aviation_scheduled", "dep_timezone_hint"], aviation.get("planned_dep", ""))
        _fill(["aviation_scheduled", "arr_timezone_hint"], aviation.get("planned_arr", ""))
    else:
        _force_fill(["schedule_local", "planned_dep"], aviation.get("planned_dep"))
        _force_fill(["schedule_local", "planned_arr"], aviation.get("planned_arr"))

    _force_fill(["actual_local", "actual_dep"], aviation.get("actual_dep"))
    _force_fill(["actual_local", "actual_arr"], aviation.get("actual_arr"))

    _fill(["flight", "operating_carrier"], aviation.get("operating_carrier"))

    reason = aviation.get("delay_reason")
    if reason and _is_unknown(p.get("delay_reason")):
        p["delay_reason"] = reason

    if reason and not _is_unknown(reason) and _is_unknown(p.get("delay_reason_is_external")):
        _INTERNAL_REASON_KEYWORDS = ["计划取消", "航空公司取消", "商业原因", "运力调整"]
        reason_lower = str(reason).lower()
        if any(kw in reason_lower for kw in _INTERNAL_REASON_KEYWORDS):
            pass
        else:
            p["delay_reason_is_external"] = "true"

    segments = aviation.get("segments")
    if segments:
        p["aviation_segments"] = segments

    avi_status = aviation.get("status")
    if avi_status and not _is_unknown(avi_status):
        p["aviation_status"] = avi_status

    p.setdefault("aviation_lookup_note", f"来自AviationStack: status={aviation.get('status')}, source={aviation.get('source')}")

    return p


def _truthy(v: Any) -> Optional[bool]:
    if v is True:
        return True
    if v is False:
        return False
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "yes", "y", "1"}:
            return True
        if s in {"false", "no", "n", "0"}:
            return False
        if s in {"unknown", ""}:
            return None
    return None


def _has_timezone(value: str) -> bool:
    """判断时间字符串是否包含时区信息（ISO8601 格式的 +HH:MM 或 Z 结尾）"""
    if not value:
        return False
    return "Z" in value or "+" in value or value.count("-") > 3


def _parse_utc_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    s = str(value).strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    if not s or s.lower() == "unknown":
        return None
    if not _has_timezone(s):
        return None
    s2 = s.replace(" ", "T")
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_tz_offset(tz_hint: Any) -> Optional[timezone]:
    if not tz_hint:
        return None
    s = str(tz_hint).strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    if not s or s.lower() == "unknown":
        return None
    m = re.search(r"(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", s, flags=re.IGNORECASE)
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    hours = int(m.group(2))
    minutes = int(m.group(3) or "0")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _parse_local_dt(value: Any, tz_hint: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if not s or s.lower() == "unknown":
        return None
    tz = _parse_tz_offset(tz_hint)
    if tz is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=tz).astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _parse_local_dt_iana(value: Any, iana_tz: Optional[str]) -> Optional[datetime]:
    """将无时区的本地时间串按 IANA 时区（如 Asia/Makassar）解析为 UTC。"""
    from zoneinfo import ZoneInfo
    if not value or not iana_tz or str(iana_tz).strip().lower() in ("", "unknown"):
        return None
    s = str(value).strip()
    if "/" in s:
        s = s.split("/")[0].strip()
    if not s or s.lower() == "unknown":
        return None
    if _has_timezone(s):
        return None
    try:
        zi = ZoneInfo(str(iana_tz).strip())
    except Exception:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            return dt.replace(tzinfo=zi).astimezone(timezone.utc)
        except Exception:
            continue
    return None


def _parse_threshold_minutes(policy_terms_excerpt: str) -> Optional[int]:
    """从条款要点/摘录中提取起赔门槛（分钟）。"""
    s = str(policy_terms_excerpt or "")
    patterns = [
        r"起赔标准\s*[:：]?\s*(\d+)\s*小时",
        r"延误满\s*(\d+)\s*小时",
        r"赔付门槛\s*[:：]?\s*(\d+)\s*小时",
        r"延误(?:时间)?\s*达(?:到)?\s*(\d+)\s*小时",
    ]
    for p in patterns:
        m = re.search(p, s)
        if m:
            try:
                return int(m.group(1)) * 60
            except Exception:
                continue
    return None


def _extract_delay_minutes_from_text(free_text: str) -> Optional[int]:
    """从事故描述/自由文本中提取延误时长（分钟），作为代码计算失败时的兜底。"""
    s = str(free_text or "").strip()
    if not s:
        return None

    m = re.search(r"延误.*?(\d+)\s*小时\s*(\d+)\s*(?:分钟|分|min)", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))

    m = re.search(r"延误.*?(\d+(?:\.\d+)?)\s*个?\s*小时", s)
    if m:
        return int(float(m.group(1)) * 60)

    m = re.search(r"延误.*?(\d+)\s*(?:分钟|分|min)", s)
    if m:
        return int(m.group(1))

    m = re.search(r"delay.*?(\d+(?:\.\d+)?)\s*hours?", s, re.IGNORECASE)
    if m:
        return int(float(m.group(1)) * 60)

    return None
