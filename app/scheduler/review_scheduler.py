#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审核调度器
定期审核待处理案件
"""

import logging
import os
import asyncio
import json
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from app.config import config
from app.state.status_manager import get_status_manager, StatusManager
from app.state.constants import ClaimStatus, ReviewStatus
from app.db.models import ClaimStatusRecord, SchedulerLog, TaskType, TaskStatus
from app.db.database import get_scheduler_log_dao, get_db_connection
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.policy_terms_registry import POLICY_TERMS
from app.output.frontend_pusher import push_to_frontend

LOGGER = logging.getLogger(__name__)


class ReviewScheduler:
    """审核调度器"""

    def __init__(
        self,
        status_manager: Optional[StatusManager] = None,
        batch_size: int = 3
    ):
        self.status_manager = status_manager or get_status_manager()
        self.batch_size = batch_size
        self.db = get_db_connection()
        self.scheduler_log_dao = get_scheduler_log_dao()

    async def initialize(self):
        """初始化"""
        await self.db.initialize()
        LOGGER.info("审核调度器初始化完成")

    async def process_pending_reviews(self, limit: int = 10) -> Tuple[int, str]:
        """
        处理待审核案件

        Args:
            limit: 限制数量

        Returns:
            (成功数量, 消息)
        """
        LOGGER.info(f"开始处理待审核案件 (限制: {limit})")

        # 创建任务日志
        log = SchedulerLog(
            task_type=TaskType.REVIEW,
            start_time=datetime.now(),
            status=TaskStatus.RUNNING
        )
        log_id = await self.scheduler_log_dao.create_log(log)

        success_count = 0
        failed_count = 0
        processed_count = 0
        error_message = None

        try:
            # 1. 获取待审核案件（只取已启用险种）
            pending_claims = await self.status_manager.get_pending_claims(
                status_filter=[ClaimStatus.DOWNLOADED, ClaimStatus.REVIEW_PENDING],
                limit=limit
            )
            # 按 ENABLED_CLAIM_TYPES 过滤，未启用的险种跳过
            enabled_types = config.ENABLED_CLAIM_TYPES
            pending_claims = [c for c in pending_claims if c.get('claim_type') in enabled_types]
            queue_depth = len(pending_claims)
            LOGGER.info(f"review queue snapshot: pending={queue_depth}, limit={limit}, configured_batch_size={self.batch_size}")

            if not pending_claims:
                LOGGER.info("没有待审核案件")
                await self.scheduler_log_dao.update_log(
                    log_id, TaskStatus.SUCCESS, 0, 0, 0, None
                )
                return 0, "没有待审核案件"

            LOGGER.info(f"找到 {len(pending_claims)} 个待审核案件")

            # 2. 批量审核
            for claim_index, claim in enumerate(pending_claims, 1):
                remaining = len(pending_claims) - claim_index
                LOGGER.info(f"review queue progress: {claim_index}/{len(pending_claims)}, remaining={remaining}")
                processed_count += 1
                forceid = claim.get('forceid', '')

                try:
                    # 状态机：downloaded -> review_pending -> reviewing
                    await self.status_manager.update_claim_status(
                        forceid,
                        ClaimStatus.REVIEW_PENDING,
                        "准备审核"
                    )
                    await self.status_manager.update_claim_status(
                        forceid,
                        ClaimStatus.REVIEWING,
                        "开始审核"
                    )

                    # 执行审核
                    result = await self._review_claim(claim)

                    # 保存审核结果到 JSON 文件（供人工复核和报告生成使用）
                    if result:
                        claim_type = claim.get('claim_type', 'flight_delay')
                        output_dir = config.REVIEW_RESULTS_DIR / claim_type
                        output_dir.mkdir(parents=True, exist_ok=True)
                        result_file = output_dir / f"{forceid}_ai_review.json"
                        result_file.write_text(
                            json.dumps(result, ensure_ascii=False, indent=2),
                            encoding='utf-8'
                        )
                        LOGGER.info(f"审核结果已保存: {result_file}")

                        # 推送审核结果到前端接口
                        try:
                            async with aiohttp.ClientSession(trust_env=True) as _session:
                                push_result = await push_to_frontend(result, _session)
                            if push_result.get("success"):
                                LOGGER.info(f"✓ 推送前端成功: {forceid}")
                            else:
                                LOGGER.warning(f"推送前端失败: {forceid}, 响应: {push_result.get('response', '')[:200]}")
                        except Exception as _push_err:
                            LOGGER.warning(f"推送前端异常: {forceid}, 错误: {_push_err}")

                    # 更新审核结果
                    success, message = await self.status_manager.update_review_status(
                        forceid,
                        result,
                        success=True
                    )

                    if success:
                        success_count += 1
                        LOGGER.info(f"审核成功: {forceid}")
                    else:
                        failed_count += 1
                        LOGGER.error(f"审核失败: {forceid} - {message}")

                except Exception as e:
                    failed_count += 1
                    error_msg = str(e)
                    LOGGER.error(f"审核异常: {forceid} - {error_msg}")

                    await self.status_manager.update_review_status(
                        forceid,
                        {},
                        success=False,
                        error_message=error_msg
                    )

            # 3. 更新任务日志
            status = TaskStatus.SUCCESS if failed_count == 0 else TaskStatus.FAILED
            if failed_count > 0:
                error_message = f"{failed_count} 个案件审核失败"

            await self.scheduler_log_dao.update_log(
                log_id, status, processed_count, success_count, failed_count, error_message
            )

            message = f"审核完成: 成功 {success_count}, 失败 {failed_count}"
            LOGGER.info(message)
            return success_count, message

        except Exception as e:
            error_message = str(e)
            LOGGER.error(f"审核任务异常: {e}")

            await self.scheduler_log_dao.update_log(
                log_id, TaskStatus.FAILED, processed_count, success_count, failed_count, error_message
            )

            return 0, f"任务异常: {error_message}"

    async def _review_claim(self, claim: Dict[str, Any]) -> Dict[str, Any]:
        """
        审核单个案件

        Args:
            claim: 案件状态详情（来自 get_pending_claims）

        Returns:
            审核结果
        """
        forceid = claim.get('forceid', '')
        claim_type = claim.get('claim_type', 'flight_delay')

        LOGGER.info(f"审核案件: {forceid} ({claim_type})")

        # 找到案件目录
        claim_folder = self._find_claim_folder(forceid)
        if not claim_folder:
            # 尝试通过 API 即时下载（在线程池中执行，避免与 asyncio event loop 冲突）
            LOGGER.info(f"本地未找到案件目录，尝试通过 API 下载: {forceid}")
            try:
                import concurrent.futures
                from scripts.fetch_claim_by_forceid import fetch_by_forceid
                from scripts.download_claims import ClaimDownloader

                def _sync_download():
                    claim_data = fetch_by_forceid(forceid)
                    downloader = ClaimDownloader(
                        api_url=os.getenv("CLAIMS_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim"),
                        output_dir=str(config.CLAIMS_DATA_DIR),
                        force_refresh=False,
                    )
                    downloader.process_claim(claim_data)

                loop = asyncio.get_event_loop()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    await loop.run_in_executor(pool, _sync_download)

                claim_folder = self._find_claim_folder(forceid)
                if claim_folder:
                    LOGGER.info(f"API 下载成功，继续审核: {forceid}")
                else:
                    LOGGER.warning(f"API 下载后仍未找到案件目录: {forceid}")
            except Exception as dl_err:
                LOGGER.warning(f"API 下载失败: {forceid} - {dl_err}")

        if not claim_folder:
            # 下载也失败，重置为待下载让下载器统一处理
            await self.status_manager.update_claim_status(
                forceid,
                ClaimStatus.DOWNLOAD_PENDING,
                "审核时找不到本地案件目录，重置为待下载"
            )
            raise FileNotFoundError(f"找不到案件目录: {forceid}，已重置为待下载")

        # 加载条款文本
        try:
            terms_file = POLICY_TERMS.resolve(claim_type)
            policy_terms = terms_file.read_text(encoding="utf-8")
        except Exception as e:
            LOGGER.warning(f"条款文件读取失败: {e}")
            policy_terms = ""

        # 使用与 run_incremental.py 相同的审核入口
        reviewer = AIClaimReviewer()
        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            for attempt in range(1, 4):
                try:
                    result = await review_claim_async(
                        reviewer, claim_folder, policy_terms, 1, 1, session
                    )
                    if result:
                        # 从 claim_info.json 注入被保险人/保单固定字段，避免依赖 AI 解析（可能乱码或缺失）
                        ci_file = claim_folder / "claim_info.json"
                        if ci_file.exists():
                            try:
                                ci = json.loads(ci_file.read_text(encoding="utf-8"))
                                result["_ci_insured_name"] = (
                                    ci.get("Insured_And_Policy") or ci.get("insured_And_Policy")
                                    or ci.get("Applicant_Name") or ci.get("applicant_Name")
                                )
                                result["_ci_id_type"] = ci.get("ID_Type") or ci.get("iD_Type")
                                result["_ci_id_number"] = ci.get("ID_Number") or ci.get("iD_Number")
                                result["_ci_policy_no"] = ci.get("PolicyNo") or ci.get("policyNo")
                                result["_ci_insurer"] = (
                                    ci.get("Insurance_Company") or ci.get("insurance_Company")
                                )
                                result["_ci_insured_amount"] = ci.get("Insured_Amount") or ci.get("insured_Amount")
                                result["_ci_remaining_coverage"] = ci.get("Remaining_Coverage") or ci.get("remaining_Coverage")
                            except Exception:
                                pass
                        return result
                except Exception as e:
                    LOGGER.warning(f"审核失败 attempt={attempt}: {e}")
                    if attempt < 3:
                        await asyncio.sleep(3)

        raise RuntimeError(f"审核彻底失败: {forceid}")

    def _find_claim_folder(self, forceid: str) -> Optional[Path]:
        """根据 forceid 在 claims_data 中找到案件目录"""
        claims_dir = config.CLAIMS_DATA_DIR

        # 主路径：遍历 claim_info.json 匹配 forceid
        for info_file in claims_dir.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
                if str(data.get("forceid") or "") == forceid:
                    return info_file.parent
            except Exception:
                continue

        # 兜底：从进度文件里找 case_no + benefitName，拼出目录路径
        progress_file = claims_dir / ".download_progress.json"
        if progress_file.exists():
            try:
                progress = json.loads(progress_file.read_text(encoding="utf-8"))
                for case_no, rec in progress.items():
                    if not isinstance(rec, dict):
                        continue
                    if rec.get("forceid") == forceid:
                        benefit_name = rec.get("benefitName", "")
                        candidate = claims_dir / benefit_name / f"{benefit_name}-案件号【{case_no}】"
                        if candidate.exists():
                            return candidate
            except Exception:
                pass

        return None

    async def retry_failed_reviews(self, limit: int = 10) -> Tuple[int, str]:
        """
        重试失败的审核

        Args:
            limit: 限制数量

        Returns:
            (重试成功数, 消息)
        """
        LOGGER.info(f"重试失败的审核任务 (限制: {limit})")

        # 获取失败的案件
        failed_claims = await self.status_manager.get_pending_claims(
            status_filter=[ClaimStatus.ERROR],
            limit=limit
        )

        if not failed_claims:
            LOGGER.info("没有需要重试的失败案件")
            return 0, "没有需要重试的失败案件"

        LOGGER.info(f"找到 {len(failed_claims)} 个需要重试的案件")

        success_count = 0
        for claim in failed_claims:
            forceid = claim.get('forceid', '')

            try:
                # 重置状态
                await self.status_manager.update_claim_status(
                    forceid,
                    ClaimStatus.REVIEW_PENDING,
                    "重试审核"
                )

                # 重新审核
                result = await self._review_claim(claim)

                # 更新结果
                success, message = await self.status_manager.update_review_status(
                    forceid,
                    result,
                    success=True
                )

                if success:
                    success_count += 1

            except Exception as e:
                LOGGER.error(f"重试审核失败: {forceid}, 错误: {e}")

        message = f"重试完成: 成功 {success_count}, 失败 {len(failed_claims) - success_count}"
        LOGGER.info(message)
        return success_count, message


# 全局实例
_review_scheduler = None


def get_review_scheduler() -> ReviewScheduler:
    """获取审核调度器实例"""
    global _review_scheduler
    if _review_scheduler is None:
        _review_scheduler = ReviewScheduler()
    return _review_scheduler


async def run_review_scheduler():
    """运行审核调度器（用于定时任务）"""
    scheduler = get_review_scheduler()
    await scheduler.initialize()

    try:
        count, message = await scheduler.process_pending_reviews()
        return count, message
    finally:
        await scheduler.db.close()


if __name__ == '__main__':
    # 测试

    async def test():
        scheduler = ReviewScheduler()
        await scheduler.initialize()

        try:
            count, message = await scheduler.process_pending_reviews(limit=5)
            print(f"结果: {message}")
        finally:
            await scheduler.db.close()

    asyncio.run(test())