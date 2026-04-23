#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用规则：保单有效期判定
来源：baggage_delay/pipeline.py:213-249 的 _check_policy_validity()，经提升后支持4时间点任一在期内
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from app.rules.base import RuleResult

RULE_ID = "common.policy_validity"
RULE_VERSION = "1.1"
DESCRIPTION = "保单有效期判定：主险状态 + 4时间点任一在期内 + 安联顺延规则"

PROMPT_BLOCK = """
【保单有效期判定规则】
以下5个时间点，任意一个落在保单有效期内即视为在保障期间内：
1. 保险单生效日期
2. 事故发生日期
3. 出境时间（首次出境日期）
4. 计划起飞时间
5. 航班日期（必须是完整的年-月-日格式，可从行程单、出入境记录、行李延误证明书、行李签收单或行李运输事故单中获取）

**航班日期来源优先级（行李延误险）**：
① 行程单/电子客票（最权威，含完整年月日）
② 出入境记录（含出境/入境时间戳）
③ 航司出具的行李延误时数及原因书面证明（通常含航班日期）
④ 行李签收单（含具体签收时间，可反推航班日期）
⑤ 行李运输事故单/PIR（含姓名、航班信息、行李牌号码，日期须逐字核对）
⑥ 以上均无 → 无法判定，需补件

**注意**：登机牌通常仅有月/日，不含年份，**不可单独用于航班日期判定**，须与行程单或其他材料交叉确认年份。

安联专属顺延/提前规则（仅适用于安联保险）：
- 若被保险人实际出境时间较原保单生效日推迟不超过15日，保单生效日变更为第一次实际出境日，满期日等期限顺延，保障期限不变。
- 若被保险人实际出境时间较原保单生效日提前不超过15日，保单生效日变更为第一次实际出境日，满期日相应提前，保障期限不变。
- 超过15日差距则按原保单有效期判定。
""".strip()


def _parse_date(value: Any) -> Optional[datetime]:
    """解析日期字符串"""
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:len(fmt.replace("%Y", "0000").replace("%m", "00")
                                         .replace("%d", "00").replace("%H", "00")
                                         .replace("%M", "00").replace("%S", "00"))], fmt)
        except Exception:
            continue
    return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    formats = [
        ("%Y-%m-%dT%H:%M:%S", 19),
        ("%Y-%m-%d %H:%M:%S", 19),
        ("%Y-%m-%d %H:%M", 16),
        ("%Y%m%d%H%M%S", 14),
        ("%Y-%m-%d", 10),
        ("%Y/%m/%d", 10),
        ("%Y%m%d", 8),
    ]
    for fmt, slen in formats:
        try:
            return datetime.strptime(s[:slen], fmt)
        except Exception:
            continue
    return None


def check(claim_info: Dict[str, Any]) -> RuleResult:
    """
    保单有效期综合判定。

    检查项：
    1. 主险合同状态（PolicyStatus / MainPolicyStatus）
    2. 保障有效期：事故日期在期内（基础）；支持安联顺延规则
    3. 4时间点任一在期内即视为承保

    Args:
        claim_info: 案件信息字典（来自 claim_info.json）

    Returns:
        RuleResult：passed=True 表示通过准入，action="continue"；
                    passed=False 表示拒赔，action="reject"
    """
    detail: Dict[str, Any] = {}

    # ① 主险合同状态
    main_status = str(
        (claim_info.get("PolicyStatus") or claim_info.get("MainPolicyStatus") or "")
    ).strip().lower()
    if main_status in {"terminated", "expired", "失效", "终止"}:
        return RuleResult(
            passed=False,
            action="reject",
            reason="拒赔：主险合同效力终止，权益同步失效",
            detail={"main_status": main_status},
        )

    # ② 保单起止日期
    eff_dt = _parse_dt(claim_info.get("Effective_Date") or claim_info.get("Insurance_Period_From"))
    exp_dt = _parse_dt(claim_info.get("Expiry_Date") or claim_info.get("Insurance_Period_To"))
    detail["effective_date"] = str(eff_dt) if eff_dt else None
    detail["expiry_date"] = str(exp_dt) if exp_dt else None

    if not (eff_dt and exp_dt):
        # 缺字段暂不拦截，交由后续 AI 判断
        return RuleResult(
            passed=True,
            action="continue",
            reason="保单起止日期字段缺失，暂不拦截",
            detail=detail,
        )

    # ③ 安联顺延规则（若有首次出境时间）
    insurer = str(
        claim_info.get("Insurer") or claim_info.get("Insurance_Company") or ""
    ).lower()
    is_allianz = "安联" in insurer or "allianz" in insurer
    first_exit_raw = claim_info.get("First_Exit_Date") or claim_info.get("first_exit_date")
    applied_eff = eff_dt
    applied_exp = exp_dt
    used_extension = False

    if is_allianz and first_exit_raw:
        fex = _parse_dt(first_exit_raw)
        if fex:
            diff_days = (fex.date() - eff_dt.date()).days
            if abs(diff_days) <= 15:
                applied_eff = eff_dt + timedelta(days=diff_days)
                applied_exp = exp_dt + timedelta(days=diff_days)
                used_extension = True
                detail["allianz_extension_days"] = diff_days
    # 安联无出境记录时不补件，直接用原始保单有效期判断
    detail["applied_effective"] = str(applied_eff)
    detail["applied_expiry"] = str(applied_exp)
    detail["used_extension"] = used_extension

    # ④ 5个时间点：任一在期内即通过
    # flight_date 必须是完整的 YYYY-MM-DD（含年份），登机牌仅有月日则不接受
    flight_date_raw = claim_info.get("Flight_Date") or claim_info.get("Policy_FlightDate")
    flight_date_str = str(flight_date_raw).strip() if flight_date_raw else ""
    flight_date_dt = None
    if flight_date_str and flight_date_str.lower() not in ("", "unknown"):
        parsed_fd = _parse_dt(flight_date_str)
        # 只接受含4位年份的完整日期（YYYY-xx-xx 或 YYYY/xx/xx 或 YYYYxxxxxxxx）
        first_segment = flight_date_str.replace("/", "-").replace(" ", "-").split("-")[0]
        if parsed_fd and len(first_segment) == 4 and first_segment.isdigit():
            flight_date_dt = parsed_fd

    detail["flight_date_raw"] = flight_date_str or None
    detail["flight_date_complete"] = flight_date_dt is not None

    time_points = {
        "accident_date": _parse_dt(claim_info.get("Date_of_Accident")),
        "first_exit_date": _parse_dt(first_exit_raw) if first_exit_raw else None,
        "planned_dep_time": _parse_dt(claim_info.get("Planned_Departure_Time")),
        "flight_date": flight_date_dt,
    }
    detail["time_points_checked"] = {
        k: str(v) if v else None for k, v in time_points.items()
    }

    any_in_coverage = False
    for name, tp in time_points.items():
        if tp and (applied_eff <= tp <= applied_exp):
            any_in_coverage = True
            detail["coverage_hit_by"] = name
            break

    if not any_in_coverage:
        accident_dt = time_points.get("accident_date")
        if accident_dt:
            # 事故日期明确且超期 → 硬拒（优先级最高）
            return RuleResult(
                passed=False,
                action="reject",
                reason="拒赔：事故发生时间超出保单保障有效期",
                detail=detail,
            )
        # 无完整航班日期（含年份）→ 一律补件，不论其他时间点是否存在
        # 航班日期是判定保单有效期的核心依据，必须从行程单/出入境记录/行李延误证明/签收单/PIR 中获取
        if not flight_date_dt:
            return RuleResult(
                passed=False,
                action="supplement",
                reason=(
                    "补件：材料中缺少含完整年-月-日的原航班计划起飞日期，无法判定保单有效期。"
                    "请提供以下任一材料：行程单/电子客票、出入境记录、"
                    "航司出具的行李延误书面证明、行李签收单（含签收时间）或行李运输事故单（PIR）。"
                    "注：登机牌通常仅有月/日，不含年份，不可单独作为依据。"
                ),
                detail=detail,
            )
        # flight_date 存在但超期，其余时间点均超期或缺失 → 暂不拦截，交 AI 判断
        return RuleResult(
            passed=True,
            action="continue",
            reason="保障时间点均超出有效期或缺失，暂不拦截",
            detail=detail,
        )

    return RuleResult(
        passed=True,
        action="continue",
        reason=f"保单有效期校验通过（命中时间点: {detail.get('coverage_hit_by', 'unknown')}）",
        detail=detail,
    )
