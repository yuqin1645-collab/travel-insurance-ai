"""
baggage_delay stages — 纯工具函数（parsing、formatting、classification、result building）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        s = str(value).strip().replace(",", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _extract_delay_hours(text: str) -> Optional[float]:
    if not text:
        return None
    candidates: List[float] = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:小时|小時|h|hour|hours)", text, flags=re.IGNORECASE):
        candidates.append(float(m.group(1)))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:天|day|days)", text, flags=re.IGNORECASE):
        candidates.append(float(m.group(1)) * 24.0)
    if not candidates:
        return None
    return max(candidates)


def _extract_delay_hours_from_parsed(parsed: Dict[str, Any]) -> Optional[float]:
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("delay_hours")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = re.search(r"(\d+(?:\.\d+)?)", raw)
        if m:
            return float(m.group(1))
    return None


def _parse_dt_flexible(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "unknown":
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _extract_date_yyyy_mm_dd(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    m = re.search(r"(20\d{2}[-/]\d{1,2}[-/]\d{1,2})", s)
    if not m:
        return ""
    return m.group(1).replace("/", "-")


def _collect_receipt_times(parsed: Dict[str, Any]) -> List[datetime]:
    values: List[datetime] = []
    if not isinstance(parsed, dict):
        return values
    direct = _parse_dt_flexible(parsed.get("baggage_receipt_time"))
    if direct:
        values.append(direct)
    for item in parsed.get("receipt_times") or []:
        dt = _parse_dt_flexible(item)
        if dt:
            values.append(dt)
    return values


def _classify_aviation_failure(aviation_lookup: Dict[str, Any]) -> str:
    """将官方航班查询失败分类。"""
    if not isinstance(aviation_lookup, dict) or not aviation_lookup:
        return "none"
    if aviation_lookup.get("success") is True:
        return "none"
    err = str(aviation_lookup.get("error") or "").lower()
    system_markers = ["api key", "http ", "请求失败", "timeout", "network", "ssl", "解析失败", "connection", "mcp"]
    if any(m in err for m in system_markers):
        return "system_error"
    evidence_markers = ["未找到航班", "error_code=10", "查询失败", "不支持", "返回错误"]
    if any(m in err for m in evidence_markers):
        return "evidence_gap"
    return "evidence_gap"


def _extract_file_names(claim_info: Dict[str, Any]) -> List[str]:
    from urllib.parse import unquote, urlparse
    names: List[str] = []
    for item in claim_info.get("FileList") or []:
        if not isinstance(item, dict):
            continue
        file_url = str(item.get("FileUrl") or "").strip()
        if not file_url:
            continue
        path_name = unquote(urlparse(file_url).path.split("/")[-1])
        if path_name:
            names.append(path_name.lower())
    return names


def _result(forceid: str, remark: str, is_additional: str, conclusions: List[Dict[str, str]], debug: Dict[str, Any]) -> Dict[str, Any]:
    if remark.startswith("审核通过") or remark.startswith("赔付"):
        audit_result = "通过"
    elif is_additional == "Y" or remark.startswith("需补件") or remark.startswith("转人工"):
        audit_result = "需补件"
    else:
        audit_result = "拒绝"
    return {
        "forceid": forceid,
        "claim_type": "baggage_delay",
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": conclusions,
        "baggage_delay_audit": {"audit_result": audit_result, "explanation": remark},
        "DebugInfo": debug,
    }
