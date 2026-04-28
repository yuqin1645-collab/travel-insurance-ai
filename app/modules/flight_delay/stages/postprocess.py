"""
flight_delay stages — 后处理（_postprocess_audit_result）。
集成全部硬校验结果，对模型输出做确定性修正。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.logging_utils import LOGGER, log_extra

from .delay_calc import _augment_with_computed_delay


def _postprocess_audit_result(
    *,
    parsed: Dict[str, Any],
    audit: Dict[str, Any],
    policy_terms_excerpt: str,
    hardcheck: Optional[Dict[str, Any]] = None,
    payout_result: Optional[Dict[str, Any]] = None,
    free_text: str = "",
) -> Dict[str, Any]:
    """对模型输出做轻量确定性修正（集成全部硬校验结果）。"""
    try:
        # 1) 阈值硬门禁
        parsed = _augment_with_computed_delay(parsed=parsed or {}, policy_terms_excerpt=policy_terms_excerpt or "", free_text=free_text)
        cd = (parsed or {}).get("computed_delay") or {}
        final_minutes = cd.get("final_minutes")
        threshold_minutes = cd.get("threshold_minutes") or 5 * 60
        threshold_met = bool(cd.get("threshold_met")) if isinstance(final_minutes, int) else None

        current = str((audit or {}).get("audit_result") or "").strip()

        # 1.1 统一把代码计算出的延误分钟写回 audit.key_data
        audit = dict(audit or {})
        audit.setdefault("key_data", {})
        if isinstance(audit["key_data"], dict) and isinstance(final_minutes, int):
            audit["key_data"]["delay_duration_minutes"] = final_minutes
        audit.setdefault("logic_check", {})
        if isinstance(audit["logic_check"], dict) and threshold_met is not None:
            audit["logic_check"]["threshold_met"] = threshold_met

        # 1.2 explanation 兜底
        if not str(audit.get("explanation") or "").strip():
            kd = audit.get("key_data") or {}
            name = kd.get("passenger_name", "")
            mins = kd.get("delay_duration_minutes", "")
            reason = kd.get("reason", "")
            parts = []
            if name:
                parts.append(f"被保险人：{name}")
            if mins:
                parts.append(f"延误时长：{mins}分钟")
            if reason:
                parts.append(f"延误原因：{reason}")
            if parts:
                audit["explanation"] = "；".join(parts)

        # ── 优先级1：硬免责条款检查 ──
        if hardcheck:
            policy_cov = hardcheck.get("policy_coverage_check") or {}
            if policy_cov.get("in_coverage") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["policy_coverage_out_triggered"] = True
                cov_note = str(policy_cov.get("note") or "原出发航班计划起飞时间不在保单有效期内")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【超出有效期】{cov_note}"
                return audit

            domestic_check = hardcheck.get("domestic_flight_check") or {}
            if domestic_check.get("is_pure_domestic_cn") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["pure_domestic_cn_triggered"] = True
                dep = domestic_check.get("dep_iata", "")
                arr = domestic_check.get("arr_iata", "")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【纯国内航班不赔】出发地 {dep} 和目的地 {arr} 均在中国大陆，本保险仅承保含国际/境外段的航班"
                return audit

            war_risk = hardcheck.get("war_risk") or {}
            if war_risk.get("is_war_risk"):
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["war_risk_triggered"] = True
                war_note = war_risk.get("note", "命中战争/冲突风险维护表")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【战争因素免责】{war_note}"
                return audit

            coverage = hardcheck.get("coverage_area") or {}
            if coverage.get("in_coverage") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["coverage_out_of_area_triggered"] = True
                cov_note = str(coverage.get("note") or "超出承保区域，不予赔付")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【承保区域不符】{cov_note}"
                return audit

            coverage_text = hardcheck.get("coverage_area_text_check") or {}
            if coverage_text.get("in_coverage") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["coverage_text_out_of_area_triggered"] = True
                cov_note = str(coverage_text.get("note") or "出境地区不在保险计划承保范围内")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【承保区域不符】{cov_note}"
                return audit

            transit = hardcheck.get("transit_check") or {}
            if transit.get("is_domestic_cn") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["transit_domestic_triggered"] = True
                iata = transit.get("iata", "")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【境内中转免责】中转地 {iata} 在境内，不予赔付"
                return audit

            missed_conn = hardcheck.get("missed_connection_check") or {}
            if missed_conn.get("is_missed_connection") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["missed_connection_triggered"] = True
                audit["audit_result"] = "拒绝"
                audit["explanation"] = "【中转接驳免责】前序航班延误导致无法搭乘后续接驳航班，不予赔付"
                return audit

            passenger_check = hardcheck.get("passenger_civil_check") or {}
            if passenger_check.get("is_passenger_civil") is False:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["non_passenger_civil_triggered"] = True
                fn = passenger_check.get("flight_no", "")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【非客运航班】航班 {fn} 非民航客运班机，不在赔付范围内"
                return audit

            inheritance_check = hardcheck.get("inheritance_check") or {}
            if inheritance_check.get("is_inheritance_suspected"):
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["inheritance_suspected"] = True
                note = str(inheritance_check.get("note") or "申请人与被保险人不一致，疑似遗产继承场景")
                if str(audit.get("audit_result") or "") not in ("拒绝",):
                    audit["audit_result"] = "需补齐资料"
                    audit["explanation"] = f"【疑似遗产继承】{note}，请补充合法继承权证明文件（遗嘱/亲属关系证明等）"
                    return audit

            capacity_check = hardcheck.get("capacity_check") or {}
            if capacity_check.get("needs_guardian"):
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["needs_guardian"] = True
                note = str(capacity_check.get("note") or "被保险人为未成年人或限制民事行为能力人")
                if str(audit.get("audit_result") or "") not in ("拒绝",):
                    audit["audit_result"] = "需补齐资料"
                    audit["explanation"] = f"【需监护人材料】{note}，请补充监护人身份证明及监护关系证明"
                    return audit

            same_day_check = hardcheck.get("same_day_policy_check") or {}
            if same_day_check.get("is_denied") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["same_day_policy_triggered"] = True
                note = str(same_day_check.get("note") or "出境当天投保，投保时刻不早于计划起飞时刻，不属于保障责任")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【同天投保免责】{note}"
                return audit

            name_check = hardcheck.get("name_match_check") or {}
            if name_check.get("match_result") == "mismatch":
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = True
                    audit["logic_check"]["name_mismatch_triggered"] = True
                note = str(name_check.get("note") or "登机牌/延误证明上的乘客姓名与保单被保险人姓名不符")
                audit["audit_result"] = "拒绝"
                audit["explanation"] = f"【姓名不符】{note}"
                return audit

            fraud_check = hardcheck.get("fraud_foreseeability_check") or {}
            if fraud_check.get("fraud_suspected") is True:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["fraud_suspected"] = True
                fraud_reason = str(fraud_check.get("reason") or "").strip() or str(fraud_check.get("note") or "").strip()
                fraud_level = str(fraud_check.get("fraud_level") or "none").strip().lower()
                if fraud_level == "confirmed":
                    if isinstance(audit["logic_check"], dict):
                        audit["logic_check"]["exclusion_triggered"] = True
                    audit["audit_result"] = "拒绝"
                    audit["explanation"] = f"【可预见因素免责】{fraud_reason}"
                    return audit
                audit.setdefault("logic_check", {})
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["exclusion_triggered"] = False

        # ── 优先级2：延误时长门槛硬检查 ──
        if isinstance(final_minutes, int) and final_minutes < int(threshold_minutes):
            audit["audit_result"] = "拒绝"
            audit["explanation"] = (
                f"【未达起赔门槛】延误时长{final_minutes}分钟，未达起赔门槛{int(threshold_minutes)}分钟（取长原则计算），不予赔付。"
            )
            return audit

        # ── 优先级3：必备材料缺失 → 补材 ──
        if hardcheck:
            req_check = hardcheck.get("required_materials_check") or {}
            missing_required = req_check.get("missing_required") or []
            scanned_all_attachments = req_check.get("scanned_all_attachments") is True
            vision_result_is_empty = req_check.get("vision_result_is_empty") is True
            if missing_required and scanned_all_attachments:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["required_materials_missing"] = missing_required
                missing_str = "、".join(missing_required)
                audit["audit_result"] = "需补齐资料"
                audit["explanation"] = f"【必备材料缺失】请补充：{missing_str}"
                return audit

            if vision_result_is_empty:
                if isinstance(audit["logic_check"], dict):
                    audit["logic_check"]["vision_result_empty"] = True
                audit["audit_result"] = "需要人工复核"
                audit["explanation"] = "【材料提取失败】Vision 无法从材料中提取关键信息，需要人工复核"
                return audit

        # ── 写回代码计算的赔付金额 ──
        if payout_result and payout_result.get("status") == "calculated":
            final_amount = payout_result.get("final_amount")
            if isinstance(final_amount, (int, float)) and final_amount >= 0:
                audit.setdefault("payout_suggestion", {})
                if isinstance(audit["payout_suggestion"], dict):
                    audit["payout_suggestion"]["amount"] = final_amount
                    audit["payout_suggestion"]["currency"] = payout_result.get("currency", "CNY")
                    audit["payout_suggestion"]["basis"] = payout_result.get("basis", "")
                    audit["payout_suggestion"]["source"] = "code_calculated"

        return audit
    except Exception as e:
        LOGGER.warning(f"_postprocess_audit_result 异常: {e}", exc_info=True)
