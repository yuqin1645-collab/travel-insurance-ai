"""
Skill D: weather.lookup_alerts
气象预警查询（可预见因素时间线）
用于：投保/订票/改签 vs 预警发布时间比对，防范恶劣天气欺诈

实现方式：
1. 本地已知预警维护表（人工维护，初期先用）
2. 降级策略：返回"建议人工确认"而非硬拒赔
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import aiohttp

from app.logging_utils import LOGGER

# ==================== 预警维护表（可配置，初期人工维护） ====================
# 格式：[{type, region, airport_iata, published_at, valid_from, valid_to, level, summary}]
# type: "typhoon" | "storm" | "strike" | "ash_cloud" | "other"
_WEATHER_ALERT_TABLE: List[Dict[str, Any]] = [
    # 示例：台风预警
    # {
    #     "type": "typhoon",
    #     "region": "华南沿海",
    #     "airport_iata": ["SZX", "CAN", "HKG", "XMN"],
    #     "published_at": "2026-07-10T06:00:00",
    #     "valid_from": "2026-07-10T06:00:00",
    #     "valid_to": "2026-07-12T18:00:00",
    #     "level": "台风红色预警",
    #     "summary": "台风XX在华南沿海登陆，将引发强风暴雨，可能造成大范围航班延误",
    # },
]


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt)
        except Exception:
            continue
    return None


def lookup_alerts_table(
    airport_iata: Optional[str] = None,
    region: Optional[str] = None,
    check_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    从本地维护表查询气象预警。
    优先按 airport_iata 匹配，其次按 region 模糊匹配。
    """
    if not _WEATHER_ALERT_TABLE:
        return []

    target_date = check_date or date.today()
    results = []

    for alert in _WEATHER_ALERT_TABLE:
        # 检查有效期
        valid_from = _parse_dt(alert.get("valid_from"))
        valid_to = _parse_dt(alert.get("valid_to"))
        if valid_from and valid_to:
            if not (valid_from.date() <= target_date <= valid_to.date()):
                continue

        # 匹配机场/地区
        matched = False
        if airport_iata:
            iata_list = alert.get("airport_iata") or []
            if airport_iata.upper() in [x.upper() for x in iata_list]:
                matched = True
        if not matched and region:
            alert_region = str(alert.get("region") or "")
            if region in alert_region or alert_region in region:
                matched = True

        if matched:
            results.append({
                "type": alert.get("type", "other"),
                "level": alert.get("level", ""),
                "published_at": alert.get("published_at", ""),
                "valid_from": alert.get("valid_from", ""),
                "valid_to": alert.get("valid_to", ""),
                "summary": alert.get("summary", ""),
                "source": "local_table",
            })

    return results


def check_foreseeability(
    published_at: Optional[str],
    action_time: Optional[str],
    action_type: str = "投保/订票",
) -> Dict[str, Any]:
    """
    判断行为（投保/订票/改签）是否发生在预警发布之后（可预见性判定）。

    Args:
        published_at: 预警发布时间（ISO格式）
        action_time: 行为时间（ISO格式）
        action_type: 行为描述（"投保" / "订票" / "改签"）

    Returns:
        {
            "is_foreseeable": bool | None,
            "published_at": str,
            "action_time": str,
            "action_type": str,
            "note": str,
        }
    """
    pa = _parse_dt(published_at)
    at = _parse_dt(action_time)

    if pa is None or at is None:
        return {
            "is_foreseeable": None,
            "published_at": published_at or "",
            "action_time": action_time or "",
            "action_type": action_type,
            "note": "关键时间点缺失，无法判定可预见性，建议人工确认",
        }

    is_foreseeable = at >= pa
    return {
        "is_foreseeable": is_foreseeable,
        "published_at": published_at,
        "action_time": action_time,
        "action_type": action_type,
        "note": (
            f"{action_type}时间({action_time})晚于预警发布时间({published_at})，属可预见因素，建议拒赔"
            if is_foreseeable
            else f"{action_type}时间({action_time})早于预警发布时间({published_at})，不属可预见因素"
        ),
    }
