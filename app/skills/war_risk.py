"""
Skill C: war.check_country_risk
战争/冲突风险查询 + 维护表
用于：战争因素拒赔闭环

实现方式：
1. 本地维护表（优先，确定性判定）—— 从 war_risk_table.json 加载，业务人员可直接编辑

落地约束：
- 维护表命中 => exclusion_triggered=true
- 未命中维护表 => 转人工
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.logging_utils import LOGGER

# ==================== 从 JSON 文件加载维护表 ====================
_TABLE_PATH = Path(__file__).parent / "war_risk_table.json"

def _load_war_risk_table() -> Dict[str, List[Dict[str, str]]]:
    """
    从 war_risk_table.json 加载战争风险维护表。
    JSON 按地区分组，此处展平为 {country_code: [...]} 格式供查询使用。
    """
    try:
        with open(_TABLE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        LOGGER.error(f"[war_risk] 加载维护表失败: {e}，使用空表")
        return {}

    result: Dict[str, List[Dict[str, str]]] = {}
    for region, countries in raw.items():
        if region.startswith("_"):  # 跳过注释字段
            continue
        if not isinstance(countries, dict):
            continue
        for cc, rules in countries.items():
            if isinstance(rules, list):
                result[cc.upper()] = rules
    return result

_WAR_RISK_TABLE: Dict[str, List[Dict[str, str]]] = _load_war_risk_table()


def _parse_date(s: str) -> Optional[date]:
    """解析 YYYYMMDD 或 YYYY-MM-DD 格式日期"""
    if not s:
        return None
    s = s.replace("-", "")
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except Exception:
        return None


def check_war_table(country_code: str, check_date: Optional[date] = None) -> Dict[str, Any]:
    """
    查询本地维护表，判断指定国家在指定日期是否在战争/冲突风险期内。

    Args:
        country_code: ISO2 国家代码，如 "UA", "AE"
        check_date: 检查日期，默认今天

    Returns:
        {
            "country_code": str,
            "is_war_risk": bool | None,   # True/False/None(未知)
            "matched_rule": dict | None,   # 命中的规则条目
            "source": "war_table" | "not_found",
            "suggestion": "reject" | "manual_review" | "none",
        }
    """
    if not country_code:
        return _war_table_unknown(country_code or "")

    cc = str(country_code).strip().upper()
    rules = _WAR_RISK_TABLE.get(cc)
    if not rules:
        return _war_table_unknown(cc)

    target_date = check_date or date.today()

    for rule in rules:
        start = _parse_date(rule.get("start", ""))
        end = _parse_date(rule.get("end", ""))
        if start and end and start <= target_date <= end:
            return {
                "country_code": cc,
                "is_war_risk": True,
                "matched_rule": rule,
                "source": "war_table",
                "suggestion": "reject",
                "note": rule.get("note", ""),
            }

    return {
        "country_code": cc,
        "is_war_risk": False,
        "matched_rule": None,
        "source": "war_table",
        "suggestion": "none",
        "note": "未命中维护表战争风险期",
    }


def _war_table_unknown(country_code: str) -> Dict[str, Any]:
    return {
        "country_code": country_code,
        "is_war_risk": None,
        "matched_rule": None,
        "source": "not_found",
        "suggestion": "manual_review",
        "note": "国家代码未在维护表中，建议人工确认",
    }
