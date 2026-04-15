"""
Skills MCP 服务包
封装供模型调用的外部能力
"""

from app.skills.flight_lookup import (
    FlightLookupSkill,
    get_flight_lookup_skill,
    flight_lookup_status,
    calculate_delay_minutes,
)
from app.skills.airport import resolve_country, check_transit_domestic
from app.skills.war_risk import check_war_table, check_country_risk
from app.skills.weather import lookup_alerts, check_foreseeability
from app.skills.policy_booking import (
    lookup_effective_window,
    check_delay_in_coverage,
    lookup_ticket_status_stub,
    lookup_rebooking_stub,
    lookup_coverage_area,
    check_delay_in_coverage_area,
    verify_evidence_basic,
)
from app.skills.compensation import tier_lookup, calculate_payout, parse_tier_config_from_terms

__all__ = [
    # Skill A: 航班权威查询
    "FlightLookupSkill",
    "get_flight_lookup_skill",
    "flight_lookup_status",
    "calculate_delay_minutes",
    # Skill B: 机场三字码解析
    "resolve_country",
    "check_transit_domestic",
    # Skill C: 战争/冲突风险
    "check_war_table",
    "check_country_risk",
    # Skill D: 气象预警
    "lookup_alerts",
    "check_foreseeability",
    # Skill E: 保单权益窗口
    "lookup_effective_window",
    "check_delay_in_coverage",
    # Skill F: 客票号使用状态
    "lookup_ticket_status_stub",
    # Skill G: 改签记录
    "lookup_rebooking_stub",
    # Skill H: 保单承保区域
    "lookup_coverage_area",
    "check_delay_in_coverage_area",
    # Skill I: 材料真实性校验
    "verify_evidence_basic",
    # 阶段10: 赔付金额核算
    "tier_lookup",
    "calculate_payout",
    "parse_tier_config_from_terms",
]
