#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产化主工作流
协调所有生产化组件，实现完整的案件处理流程
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config import config
from app.scheduler.download_scheduler import get_download_scheduler, IncrementalDownloadScheduler
from app.scheduler.review_scheduler import get_review_scheduler, ReviewScheduler
from app.supplementary.handler import get_supplementary_handler, SupplementaryHandler
from app.output.coordinator import get_output_coordinator, OutputCoordinator
from app.state.status_manager import get_status_manager, StatusManager
from app.db.database import get_db_connection

LOGGER = logging.getLogger(__name__)


def _detect_claim_type(benefit: str) -> str:
    text = str(benefit or "")
    if "行李延误" in text:
        return "baggage_delay"
    if "航班延误" in text or "延误" in text:
        return "flight_delay"
    return "baggage_damage"


class ProductionWorkflow:
    """生产化主工作流"""

    def __init__(
        self,
        download_scheduler: Optional[IncrementalDownloadScheduler] = None,
        review_scheduler: Optional[ReviewScheduler] = None,
        supplementary_handler: Optional[SupplementaryHandler] = None,
        output_coordinator: Optional[OutputCoordinator] = None,
        status_manager: Optional[StatusManager] = None
    ):
        self.download_scheduler = download_scheduler or get_download_scheduler()
        self.review_scheduler = review_scheduler or get_review_scheduler()
        self.supplementary_handler = supplementary_handler or get_supplementary_handler()
        self.output_coordinator = output_coordinator or get_output_coordinator()
        self.status_manager = status_manager or get_status_manager()
        self.db = get_db_connection()

        # 运行状态
        self.is_running = False
        self.last_run_time = None
        self._is_shutting_down = False

    async def initialize(self):
        """初始化所有组件"""
        LOGGER.info("初始化生产化工作流...")

        # 初始化数据库连接
        await self.db.initialize()

        # 初始化各个调度器
        await self.download_scheduler.initialize()
        await self.review_scheduler.initialize()
        await self.supplementary_handler.initialize()
        await self.output_coordinator.initialize()

        LOGGER.info("✓ 生产化工作流初始化完成")

    async def run_hourly_check(self) -> Dict[str, Any]:
        """
        每小时执行的主检查流程

        Returns:
            执行结果汇总
        """
        if self.is_running:
            LOGGER.warning("上一次检查仍在运行，跳过本次检查")
            return {
                "status": "skipped",
                "reason": "上一次检查仍在运行"
            }

        self.is_running = True
        self.last_run_time = datetime.now()

        LOGGER.info("=" * 60)
        LOGGER.info("开始每小时检查流程")
        LOGGER.info("=" * 60)

        results = {
            "start_time": self.last_run_time.isoformat(),
            "tasks": {}
        }

        try:
            # 检查是否正在关闭
            if getattr(self, '_is_shutting_down', False):
                LOGGER.info("系统正在关闭，跳过本次检查")
                return {"status": "skipped", "reason": "系统正在关闭"}
            # 0. 孤儿案件兜底扫描（已下载但未进入审核队列的案件）
            LOGGER.info("\n[0/8] 孤儿案件兜底扫描...")
            orphan_result = await self._orphan_sweep()
            results["tasks"]["orphan_sweep"] = orphan_result

            # 1. 增量下载新案件
            LOGGER.info("\n[1/8] 增量下载新案件...")
            download_count, download_msg = await self.download_scheduler.run_hourly_check()
            results["tasks"]["download"] = {
                "count": download_count,
                "message": download_msg
            }

            # 2. 审核待处理案件
            LOGGER.info("\n[2/8] 审核待处理案件...")
            review_count, review_msg = await self.review_scheduler.process_pending_reviews()
            results["tasks"]["review"] = {
                "count": review_count,
                "message": review_msg
            }

            # 3. 检查补件状态
            LOGGER.info("\n[3/8] 检查补件状态...")
            supplementary_result = await self.supplementary_handler.check_supplementary_deadline()
            results["tasks"]["supplementary_reminder"] = {
                "count": supplementary_result[0],
                "message": supplementary_result[1]
            }

            # 4. 检查补件超时
            LOGGER.info("\n[4/8] 检查补件超时...")
            timeout_result = await self.supplementary_handler.check_supplementary_timeout()
            results["tasks"]["supplementary_timeout"] = {
                "count": timeout_result[0],
                "message": timeout_result[1]
            }

            # 5. 检查收到补件
            LOGGER.info("\n[5/8] 检查收到补件...")
            received_result = await self.supplementary_handler.check_supplementary_received()
            results["tasks"]["supplementary_received"] = {
                "count": received_result[0],
                "message": received_result[1]
            }

            # 6. 同步审核结果到数据库
            LOGGER.info("\n[6/8] 同步审核结果到数据库...")
            sync_count, sync_fail = await asyncio.get_event_loop().run_in_executor(
                None, self._sync_review_results_to_db
            )
            results["tasks"]["sync_db"] = {
                "count": sync_count,
                "message": f"同步成功 {sync_count}，失败 {sync_fail}"
            }

            # 7. 同步人工处理状态
            LOGGER.info("\n[7/8] 同步人工处理状态...")
            manual_count, manual_fail = await asyncio.get_event_loop().run_in_executor(
                None, self._sync_manual_status
            )
            results["tasks"]["sync_manual"] = {
                "count": manual_count,
                "message": f"同步成功 {manual_count}，失败 {manual_fail}"
            }

            # 汇总结果
            results["end_time"] = datetime.now().isoformat()
            results["status"] = "success"
            results["summary"] = {
                "orphan_swept": orphan_result.get("registered_count", 0),
                "downloaded": download_count,
                "reviewed": review_count,
                "supplementary_reminded": supplementary_result[0],
                "supplementary_timeout": timeout_result[0],
                "supplementary_received": received_result[0],
                "db_synced": sync_count,
                "manual_synced": manual_count,
            }

            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("每小时检查流程完成")
            LOGGER.info("=" * 60)
            LOGGER.info(f"兜底注册: {orphan_result.get('registered_count',0)} | 下载: {download_count} | 审核: {review_count} | 补件提醒: {supplementary_result[0]} | 超时: {timeout_result[0]} | 收到: {received_result[0]} | DB同步: {sync_count} | 人工状态: {manual_count}")
            LOGGER.info("=" * 60)

            return results

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"每小时检查流程异常: {error_msg}", exc_info=True)

            results["status"] = "failed"
            results["error"] = error_msg
            results["end_time"] = datetime.now().isoformat()

            return results

        finally:
            self.is_running = False

    def _sync_review_results_to_db(self) -> tuple:
        """同步本地审核结果JSON到数据库（同步方法，供executor调用）"""
        import json
        import pymysql
        import os
        from datetime import datetime as _dt

        results_dir = config.REVIEW_RESULTS_DIR
        claims_dir = config.CLAIMS_DATA_DIR

        # 构建 forceid -> claim_info 缓存
        info_cache = {}
        for f in claims_dir.rglob("claim_info.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                fid = str(data.get("forceid") or "").strip()
                if fid:
                    info_cache[fid] = data
            except Exception:
                pass

        # 加载所有审核结果
        json_files = list(results_dir.rglob("*_ai_review.json"))
        if not json_files:
            return 0, 0

        conn = pymysql.connect(
            host=os.getenv("DB_HOST", ""),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "ai"),
            charset="utf8mb4",
        )
        success = fail = 0
        try:
            with conn.cursor() as cur:
                for jf in json_files:
                    try:
                        data = json.loads(jf.read_text(encoding="utf-8"))
                        forceid = data.get("forceid", "")
                        if not forceid:
                            continue
                        claim_info = info_cache.get(forceid, {})
                        fields = self._extract_review_fields(data, claim_info)

                        # 构建完整字段列表
                        keys = list(fields.keys())
                        placeholders = ", ".join(["%s"] * len(keys))
                        update_clause = ", ".join(
                            [f"{k}=VALUES({k})" for k in keys if k != "forceid"]
                        )
                        sql = (
                            f"INSERT INTO ai_review_result ({', '.join(keys)}) "
                            f"VALUES ({placeholders}) "
                            f"ON DUPLICATE KEY UPDATE {update_clause}, updated_at=CURRENT_TIMESTAMP"
                        )
                        cur.execute(sql, list(fields.values()))
                        success += 1
                    except Exception as e:
                        fail += 1
                        LOGGER.warning(f"同步审核结果失败 {jf.name}: {e}")
            conn.commit()
        finally:
            conn.close()
        return success, fail

    @staticmethod
    def _parse_dt(value):
        """解析日期时间字符串，返回 datetime 或 None"""
        if not value:
            return None
        from datetime import datetime as _dt
        if isinstance(value, _dt):
            return value
        try:
            return _dt.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    def _extract_review_fields(self, data: dict, claim_info: dict) -> dict:
        """从审核结果JSON提取所有数据库字段"""
        import json
        from datetime import datetime as _dt

        fields = {
            "forceid": data.get("forceid", ""),
            "claim_id": (
                data.get("ClaimId") or data.get("claim_id")
                or claim_info.get("ClaimId") or ""
            ),
        }

        # benefit_name
        fields["benefit_name"] = (
            claim_info.get("BenefitName") or claim_info.get("benefit_name") or ""
        )

        # flight_delay_audit 部分
        audit = (
            data.get("flight_delay_audit")
            or data.get("DebugInfo", {}).get("flight_delay_audit")
            or {}
        )
        if audit:
            fields["audit_result"] = audit.get("audit_result", "")
            fields["audit_status"] = "completed" if audit.get("audit_result") else "pending"
            fields["confidence_score"] = audit.get("confidence_score")
            fields["audit_time"] = _dt.now() if audit.get("audit_result") else None
            fields["auditor"] = "AI系统"

            # 逻辑校验
            logic_check = audit.get("logic_check", {})
            fields["identity_match"] = "Y" if logic_check.get("identity_match") else "N"
            fields["threshold_met"] = "Y" if logic_check.get("threshold_met") else "N"
            fields["exclusion_triggered"] = "Y" if logic_check.get("exclusion_triggered") else "N"
            fields["exclusion_reason"] = logic_check.get("exclusion_reason", "")

            # key_data
            key_data = audit.get("key_data", {})
            fields["passenger_name"] = key_data.get("passenger_name", "")
            fields["delay_duration_minutes"] = key_data.get("delay_duration_minutes")
            fields["delay_reason"] = key_data.get("reason", "")

            # 赔付
            payout = audit.get("payout_suggestion", {})
            fields["payout_amount"] = payout.get("amount")
            fields["payout_currency"] = payout.get("currency", "CNY")
            fields["payout_basis"] = payout.get("basis", "")

            # 说明
            fields["decision_reason"] = audit.get("explanation", "")
            fields["final_decision"] = audit.get("final_decision", "")

        # DebugInfo 部分
        debug_info = data.get("DebugInfo", {})

        # flight_delay_parse - 最完整的数据源
        parse = debug_info.get("flight_delay_parse", {})
        if parse:
            # 乘客信息
            passenger = parse.get("passenger", {})
            if not fields.get("passenger_name"):
                fields["passenger_name"] = passenger.get("name", "")
            fields["passenger_id_type"] = passenger.get("id_type", "")
            fields["passenger_id_number"] = passenger.get("id_number", "")

            # 保单信息
            policy_hint = parse.get("policy_hint", {})
            fields["policy_no"] = policy_hint.get("policy_no", "")
            fields["insurer"] = policy_hint.get("insurer", "")
            fields["policy_effective_date"] = policy_hint.get("policy_effective_date")
            fields["policy_expiry_date"] = policy_hint.get("policy_expiry_date")

            # 航班信息
            flight = parse.get("flight", {})
            if not fields.get("flight_no"):
                fields["flight_no"] = (
                    flight.get("ticket_flight_no") or flight.get("operating_flight_no", "")
                )
            fields["operating_carrier"] = flight.get("operating_carrier", "")

            # 航线信息
            route = parse.get("route", {})
            if not fields.get("dep_iata"):
                fields["dep_iata"] = route.get("dep_iata", "")
            if not fields.get("arr_iata"):
                fields["arr_iata"] = route.get("arr_iata", "")
            fields["dep_city"] = route.get("dep_city", "")
            fields["arr_city"] = route.get("arr_city", "")
            fields["dep_country"] = route.get("dep_country", "")
            fields["arr_country"] = route.get("arr_country", "")

            # 时间信息
            schedule = parse.get("schedule_local", {})
            actual = parse.get("actual_local", {})
            alt = parse.get("alternate_local", {})
            # 原航班：首次购票计划时刻（schedule_local）
            fields["planned_dep_time"] = self._parse_dt(schedule.get("planned_dep"))
            fields["planned_arr_time"] = self._parse_dt(schedule.get("planned_arr"))
            # 原航班：飞常准实际时刻（actual_local，已在pipeline中以飞常准为优先写入）
            fields["actual_dep_time"] = self._parse_dt(actual.get("actual_dep"))
            fields["actual_arr_time"] = self._parse_dt(actual.get("actual_arr"))
            # 被保险人实际乘坐航班时刻（改签/替代航班）
            fields["alt_dep_time"] = self._parse_dt(alt.get("alt_dep"))
            fields["alt_arr_time"] = self._parse_dt(alt.get("alt_arr"))
            # 实际乘坐航班号和路由
            alt_fn = str(alt.get("alt_flight_no") or "").strip()
            if alt_fn and alt_fn.lower() not in ("unknown", "null", "none", ""):
                fields["alt_flight_no"] = alt_fn
            alt_dep_iata = str(alt.get("alt_dep_iata") or "").strip()
            alt_arr_iata = str(alt.get("alt_arr_iata") or "").strip()
            if alt_dep_iata and alt_dep_iata.lower() != "unknown":
                fields["alt_dep_iata"] = alt_dep_iata
            if alt_arr_iata and alt_arr_iata.lower() != "unknown":
                fields["alt_arr_iata"] = alt_arr_iata

            # 航班场景标签
            is_connecting = parse.get("is_connecting_flight")
            has_alt = bool(fields.get("alt_flight_no") or fields.get("alt_dep_time"))
            rebooking_cnt = parse.get("rebooking_count") or 0
            if not has_alt and not is_connecting:
                fields["flight_scenario"] = "direct"
            elif is_connecting and not has_alt:
                fields["flight_scenario"] = "connecting"
            elif has_alt and int(rebooking_cnt) <= 1:
                fields["flight_scenario"] = "rebooking"
            elif has_alt and int(rebooking_cnt) > 1:
                fields["flight_scenario"] = "multi_rebooking"
            fields["rebooking_count"] = int(rebooking_cnt)

            # 延误计算追溯
            delay_meta = parse.get("delay_calculation_meta", {})
            if delay_meta:
                fields["delay_calc_from"] = delay_meta.get("from_field", "")
                fields["delay_calc_to"] = delay_meta.get("to_field", "")

        # flight_delay_aviation_lookup - 飞常准原航班独立字段（不再混入 planned/actual）
        lookup = debug_info.get("flight_delay_aviation_lookup", {})
        if lookup and lookup.get("success"):
            # 飞常准原航班独立存储
            fields["avi_status"] = lookup.get("status", "")
            fields["avi_planned_dep"] = self._parse_dt(lookup.get("planned_dep"))
            fields["avi_planned_arr"] = self._parse_dt(lookup.get("planned_arr"))
            fields["avi_actual_dep"] = self._parse_dt(lookup.get("actual_dep"))
            fields["avi_actual_arr"] = self._parse_dt(lookup.get("actual_arr"))
            # 基础路由信息补填（只填空）
            if not fields.get("flight_no"):
                fields["flight_no"] = lookup.get("flight_no", "")
            if not fields.get("dep_iata"):
                fields["dep_iata"] = lookup.get("dep_iata", "")
            if not fields.get("arr_iata"):
                fields["arr_iata"] = lookup.get("arr_iata", "")
            # planned/actual 仍做兜底（若 parse 未填）
            if not fields.get("planned_dep_time"):
                fields["planned_dep_time"] = self._parse_dt(lookup.get("planned_dep"))
            if not fields.get("planned_arr_time"):
                fields["planned_arr_time"] = self._parse_dt(lookup.get("planned_arr"))
            if not fields.get("actual_dep_time"):
                fields["actual_dep_time"] = self._parse_dt(lookup.get("actual_dep"))
            if not fields.get("actual_arr_time"):
                fields["actual_arr_time"] = self._parse_dt(lookup.get("actual_arr"))
            if lookup.get("status") == "取消":
                fields["delay_type"] = "cancelled"
            if not fields.get("delay_duration_minutes"):
                fields["delay_duration_minutes"] = lookup.get("delay_minutes")

        # flight_delay_aviation_lookup - 飞常准替代航班（若有 alt_lookup 子键）
        alt_lookup = debug_info.get("flight_delay_alt_aviation_lookup", {})
        if not alt_lookup:
            # 兼容：从 lookup 本身取 alt 字段
            alt_lookup = lookup.get("alt_flight_lookup", {}) if lookup else {}
        if alt_lookup and alt_lookup.get("success"):
            fields["avi_alt_flight_no"] = alt_lookup.get("flight_no", "")
            fields["avi_alt_planned_dep"] = self._parse_dt(alt_lookup.get("planned_dep"))
            fields["avi_alt_actual_dep"] = self._parse_dt(alt_lookup.get("actual_dep"))
            fields["avi_alt_actual_arr"] = self._parse_dt(alt_lookup.get("actual_arr"))

        # flight_delay_vision_extract - 视觉提取补充
        vision = debug_info.get("flight_delay_vision_extract", {})
        if vision:
            flights_found = vision.get("all_flights_found", [])
            if flights_found and not fields.get("flight_no"):
                first = flights_found[0]
                fields["flight_no"] = first.get("flight_no", "")
                if not fields.get("dep_iata"):
                    fields["dep_iata"] = first.get("dep_iata", "")
                if not fields.get("arr_iata"):
                    fields["arr_iata"] = first.get("arr_iata", "")
            # 替代航班路由（vision alternate）
            v_alt = vision.get("alternate") or {}
            if not fields.get("alt_flight_no"):
                v_alt_fn = str(v_alt.get("alt_flight_no") or "").strip()
                if v_alt_fn and v_alt_fn.lower() not in ("unknown", "null", "none", ""):
                    fields["alt_flight_no"] = v_alt_fn
            if not fields.get("alt_dep_iata"):
                v_alt_dep = str(vision.get("dep_iata") or v_alt.get("alt_dep_iata") or "").strip()
                if v_alt_dep and v_alt_dep.lower() != "unknown":
                    fields["alt_dep_iata"] = v_alt_dep
            if not fields.get("alt_arr_iata"):
                v_alt_arr = str(vision.get("arr_iata") or v_alt.get("alt_arr_iata") or "").strip()
                if v_alt_arr and v_alt_arr.lower() != "unknown":
                    fields["alt_arr_iata"] = v_alt_arr

        # flight_delay_payout - 赔付信息补充
        payout_info = debug_info.get("flight_delay_payout", {})
        if payout_info:
            if not fields.get("insured_amount"):
                fields["insured_amount"] = payout_info.get("insured_amount")
            if not fields.get("remaining_coverage"):
                fields["remaining_coverage"] = payout_info.get("remaining_coverage")

        # claim_info 补充（本地文件）
        if claim_info:
            if not fields.get("insured_amount"):
                fields["insured_amount"] = claim_info.get("Insured_Amount") or claim_info.get("Amount")
            if not fields.get("remaining_coverage"):
                fields["remaining_coverage"] = claim_info.get("Remaining_Coverage")
            if not fields.get("policy_no"):
                fields["policy_no"] = claim_info.get("PolicyNo", "")
            if not fields.get("insurer"):
                fields["insurer"] = claim_info.get("Insurance_Company", "")
            if not fields.get("passenger_name"):
                fields["passenger_name"] = (
                    claim_info.get("Insured_And_Policy")
                    or claim_info.get("Insured_Name")
                    or claim_info.get("Applicant_Name", "")
                )

        # 基础字段
        fields["remark"] = (data.get("Remark") or "")[:2000]
        fields["is_additional"] = str(data.get("IsAdditional", "N"))[:1]
        fields["supplementary_count"] = data.get("supplementary_count", 0)
        fields["supplementary_reason"] = data.get("supplementary_reason") or data.get("Remark", "") if data.get("IsAdditional") == "Y" else ""
        fields["key_conclusions"] = json.dumps(data.get("KeyConclusions", []), ensure_ascii=False)
        fields["raw_result"] = json.dumps(data, ensure_ascii=False)

        # 去掉 None 键（避免向不存在的列写数据），但保���空字符串
        fields = {k: v for k, v in fields.items() if v is not None or k in (
            "passenger_name", "flight_no", "dep_iata", "arr_iata",
            "audit_result", "remark", "is_additional",
        )}

        return fields

    def _sync_manual_status(self) -> tuple:
        """查询人工处理状态并更新数据库（同步方法，供executor调用）"""
        import os
        import requests
        import pymysql

        RESULT_API_URL = "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim_Result"

        conn = pymysql.connect(
            host=os.getenv("DB_HOST", ""),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "ai"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        success = fail = 0
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT forceid FROM ai_review_result")
                rows = cur.fetchall()

            if not rows:
                return 0, 0

            forceids = [row["forceid"] for row in rows]

            # 批量查询接口
            try:
                resp = requests.post(
                    RESULT_API_URL,
                    json={"pageSize": "100", "pageIndex": "1", "data": forceids},
                    timeout=30,
                )
                resp.raise_for_status()
                raw = resp.json()
                if isinstance(raw, list):
                    items = raw
                elif isinstance(raw, dict):
                    inner = raw.get("data")
                    items = inner if isinstance(inner, list) else [raw]
                else:
                    items = []

                result_map = {}
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    fid = str(item.get("forceid") or item.get("ForceId") or "").strip()
                    if fid:
                        result_map[fid] = item
            except Exception as e:
                LOGGER.warning(f"批量查询人工状态接口失败: {e}")
                return 0, len(forceids)

            for forceid in forceids:
                data = result_map.get(forceid)
                if not data:
                    continue
                try:
                    final_status = str(data.get("Final_Status") or "").strip()
                    supplementary_reason = str(data.get("Supplementary_Reason") or "").strip()
                    approved = str(data.get("Approved_amount") or "").strip()
                    assessment_remark = str(data.get("Assessment_Remark") or "").strip()

                    if final_status == "事后理赔拒赔":
                        manual_status, manual_conclusion = "拒绝", assessment_remark
                    elif final_status == "待补件":
                        manual_status, manual_conclusion = "需补齐资料", supplementary_reason
                    elif final_status == "已补件待审核":
                        manual_status, manual_conclusion = "待定", supplementary_reason or "已补件待审核"
                    elif final_status == "线上理赔初审":
                        manual_status, manual_conclusion = "待定", approved or assessment_remark or "线上理赔初审"
                    elif final_status == "支付成功":
                        manual_status, manual_conclusion = "通过", approved
                    elif final_status == "结案待财务付款":
                        manual_status, manual_conclusion = "通过", approved or assessment_remark
                    elif final_status == "取消理赔":
                        manual_status, manual_conclusion = "拒绝", "取消理赔"
                    else:
                        manual_status, manual_conclusion = "待定", final_status

                    with conn.cursor() as cur:
                        cur.execute(
                            """UPDATE ai_review_result
                               SET manual_status=%s, manual_conclusion=%s, updated_at=CURRENT_TIMESTAMP
                               WHERE forceid=%s""",
                            (manual_status, manual_conclusion, forceid)
                        )
                    conn.commit()
                    success += 1
                except Exception as e:
                    fail += 1
                    LOGGER.warning(f"同步人工状态失败 {forceid}: {e}")
        finally:
            conn.close()
        return success, fail

    async def _orphan_sweep(self) -> Dict[str, Any]:
        """
        孤儿案件兜底扫描：扫描本地 claims_data，对已下载但未在 claim_status 表注册、
        且没有审核结果的案件，注册状态并推入审核队列。
        解决 run_incremental.py 等路径下载后未注册的问题。
        """
        import json as _json

        LOGGER.info("开始孤儿案件兜底扫描...")

        registered_count = 0
        skipped_count = 0
        errors = []

        # 已结案状态（这些不需要注册审核）
        CONCLUDED_STATUSES = {
            "零结关案", "支付成功", "事后理赔拒赔",
            "取消理赔", "结案待财务付款",
        }

        # 获取已有审核结果的 forceid
        reviewed_forceids = set()
        for f in config.REVIEW_RESULTS_DIR.rglob("*_ai_review.json"):
            fid = f.stem.replace("_ai_review", "")
            reviewed_forceids.add(fid)

        # 扫描本地所有 claim_info.json
        for info_file in config.CLAIMS_DATA_DIR.rglob("claim_info.json"):
            try:
                data = _json.loads(info_file.read_text(encoding="utf-8"))
                forceid = str(data.get("forceid") or "").strip()
                if not forceid:
                    continue

                # 跳过已有审核结果的
                if forceid in reviewed_forceids:
                    skipped_count += 1
                    continue

                # 跳过已结案的
                final_status = str(data.get("Final_Status") or "").strip()
                if final_status in CONCLUDED_STATUSES:
                    skipped_count += 1
                    continue

                # 检查 claim_status 表是否已有记录
                existing = await self.status_manager.get_claim_status(forceid)
                if existing is not None:
                    skipped_count += 1
                    continue

                # 判断案件类型
                benefit = str(data.get("BenefitName") or "")
                claim_type = _detect_claim_type(benefit)

                # 注册到审核队列
                claim_id = data.get("ClaimId") or data.get("caseNo") or forceid
                await self.status_manager.create_claim_status(
                    claim_id=claim_id,
                    forceid=forceid,
                    claim_type=claim_type,
                    initial_status="downloaded",
                )
                registered_count += 1
                LOGGER.info(f"  [兜底注册] {forceid} -> downloaded (claim_id={claim_id})")

            except Exception as e:
                errors.append(str(e))
                LOGGER.warning(f"  扫描孤儿案件异常 {info_file}: {e}")

        result = {
            "status": "completed",
            "registered_count": registered_count,
            "skipped_count": skipped_count,
            "errors": errors,
            "message": f"注册 {registered_count} 个，跳过 {skipped_count} 个"
        }
        LOGGER.info(f"孤儿扫描完成: {result['message']}")
        return result

    async def process_single_claim(
        self,
        claim_type: str,
        claim_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        处理单个案件的完整流程

        Args:
            claim_type: 案件类型
            claim_data: 案件数据

        Returns:
            处理结果
        """
        forceid = claim_data.get("forceid", "")
        LOGGER.info(f"处理单个案件: {forceid} ({claim_type})")

        results = {
            "forceid": forceid,
            "claim_type": claim_type,
            "steps": []
        }

        try:
            # 1. 创建案件状态
            await self.status_manager.create_claim_status(
                claim_id=claim_data.get("claim_id", forceid),
                forceid=forceid,
                claim_type=claim_type,
                initial_status="download_pending"
            )
            results["steps"].append({"step": "create_status", "status": "success"})

            # 2. 执行审核
            # 这里需要调用实际的审核逻辑
            # review_result = await self._review_claim(claim_type, claim_data)
            review_result = {"forceid": forceid, "Remark": "测试结果", "IsAdditional": "N"}  # 示例

            results["steps"].append({"step": "review", "status": "success", "result": review_result})

            # 3. 处理结果
            if review_result.get("IsAdditional") == "Y":
                # 需补件
                await self.supplementary_handler.handle_supplementary_needed(forceid, review_result)
                results["steps"].append({"step": "supplementary", "status": "success"})
            else:
                # 最终决定，输出结果
                output_result = await self.output_coordinator.dispatch_output(forceid, review_result)
                results["steps"].append({"step": "output", "status": "success", "result": output_result})

            results["overall_status"] = "success"
            LOGGER.info(f"✓ 单个案件处理完成: {forceid}")

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"✗ 单个案件处理失败: {forceid}, 错误: {error_msg}")
            results["overall_status"] = "failed"
            results["error"] = error_msg

        return results

    async def run_cleanup(self, days_to_keep: int = 30) -> Dict[str, Any]:
        """
        运行清理任务

        Args:
            days_to_keep: 保留天数

        Returns:
            清理结果
        """
        LOGGER.info(f"运行清理任务: 保留 {days_to_keep} 天数据")

        results = {
            "days_to_keep": days_to_keep,
            "tasks": {}
        }

        try:
            # 1. 清理过期案件状态
            count, msg = await self.status_manager.cleanup_expired_claims(days_to_keep)
            results["tasks"]["claim_status"] = {"count": count, "message": msg}

            # 2. 清理下载进度缓存
            # TODO: 实现清理逻辑

            # 3. 清理临时文件
            # TODO: 实现清理逻辑

            results["status"] = "success"
            LOGGER.info("清理任务完成")

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"清理任务失败: {error_msg}")
            results["status"] = "failed"
            results["error"] = error_msg

        return results

    async def get_system_status(self) -> Dict[str, Any]:
        """
        获取系统状态

        Returns:
            系统状态信息
        """
        LOGGER.info("获取系统状态...")

        status = {
            "timestamp": datetime.now().isoformat(),
            "is_running": self.is_running,
            "last_run_time": self.last_run_time.isoformat() if self.last_run_time else None,
            "components": {
                "database": "connected" if self.db.pool else "disconnected",
                "download_scheduler": "initialized",
                "review_scheduler": "initialized",
                "supplementary_handler": "initialized",
                "output_coordinator": "initialized"
            },
            "config": {
                "download_interval": config.DOWNLOAD_INTERVAL,
                "review_interval": config.REVIEW_INTERVAL,
                "supplementary_check_interval": config.SUPPLEMENTARY_CHECK_INTERVAL,
                "max_supplementary_count": config.MAX_SUPPLEMENTARY_COUNT
            }
        }

        # 获取案件统计
        try:
            statistics = await self.status_manager.get_claim_statistics()
            status["statistics"] = statistics
        except Exception as e:
            LOGGER.error(f"获取案件统计失败: {e}")
            status["statistics"] = {"error": str(e)}

        return status

    async def shutdown(self):
        """关闭系统"""
        LOGGER.info("关闭生产化工作流...")

        self._is_shutting_down = True
        self.is_running = False

        # 关闭数据库连接
        await self.db.close()

        LOGGER.info("✓ 生产化工作流已关闭")


# 全局实例
_production_workflow = None


def get_production_workflow() -> ProductionWorkflow:
    """获取生产化工作流实例"""
    global _production_workflow
    if _production_workflow is None:
        _production_workflow = ProductionWorkflow()
    return _production_workflow


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='航班延误AI审核系统生产化工作流')
    parser.add_argument('--mode', type=str, default='hourly',
                       choices=['hourly', 'status', 'cleanup', 'single'],
                       help='运行模式')
    parser.add_argument('--days', type=int, default=30,
                       help='清理模式下的保留天数')
    parser.add_argument('--forceid', type=str,
                       help='单个案件模式下的案件ID')

    args = parser.parse_args()

    # 配置日志（仅在未配置时添加handler，避免重复）
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler('production.log', encoding='utf-8', delay=False)
            ]
        )

    # 创建工作流
    workflow = get_production_workflow()

    try:
        # 初始化
        await workflow.initialize()

        # 根据模式执行
        if args.mode == 'hourly':
            result = await workflow.run_hourly_check()
            print(f"\n结果: {result}")

        elif args.mode == 'status':
            status = await workflow.get_system_status()
            print(f"\n系统状态: {status}")

        elif args.mode == 'cleanup':
            result = await workflow.run_cleanup(args.days)
            print(f"\n清理结果: {result}")

        elif args.mode == 'single':
            if not args.forceid:
                print("错误: 单个案件模式需要指定 --forceid")
                return

            # TODO: 从数据库或文件加载案件数据
            claim_data = {"forceid": args.forceid}
            result = await workflow.process_single_claim("flight_delay", claim_data)
            print(f"\n处理结果: {result}")

    except KeyboardInterrupt:
        LOGGER.info("用户中断")
    except Exception as e:
        LOGGER.error(f"主流程异常: {e}", exc_info=True)
    finally:
        await workflow.shutdown()


if __name__ == '__main__':
    asyncio.run(main())