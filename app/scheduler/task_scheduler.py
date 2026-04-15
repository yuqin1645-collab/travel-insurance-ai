#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
定时任务调度器
使用APScheduler实现定时执行
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from app.config import config
from app.production.main_workflow import get_production_workflow, ProductionWorkflow

LOGGER = logging.getLogger(__name__)


def _detect_claim_type(benefit: str) -> str:
    text = str(benefit or "")
    if "行李延误" in text:
        return "baggage_delay"
    if "航班延误" in text or "延误" in text:
        return "flight_delay"
    return "baggage_damage"


class TaskScheduler:
    """定时任务调度器"""

    def __init__(self, workflow: Optional[ProductionWorkflow] = None):
        self.workflow = workflow or get_production_workflow()
        self.scheduler = AsyncIOScheduler()
        self.is_initialized = False

        # 注册事件监听
        self.scheduler.add_listener(self._job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._job_error, EVENT_JOB_ERROR)

    async def initialize(self):
        """初始化调度器"""
        if self.is_initialized:
            return

        LOGGER.info("初始化定时任务调度器...")

        # 初始化工作流
        await self.workflow.initialize()

        # 添加定时任务
        self._add_jobs()

        self.is_initialized = True
        LOGGER.info("✓ 定时任务调度器初始化完成")

    def _add_jobs(self):
        """添加定时任务"""
        LOGGER.info("添加定时任务...")

        # 1. 主检查流程 - 每小时执行
        self.scheduler.add_job(
            self._run_hourly_check,
            trigger=IntervalTrigger(seconds=config.DOWNLOAD_INTERVAL),
            id='hourly_check',
            name='每小时检查',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info(f"  ✓ 每小时检查任务 (间隔: {config.DOWNLOAD_INTERVAL}秒)")

        # 2. 审核任务 - 每10分钟执行
        self.scheduler.add_job(
            self._run_review,
            trigger=IntervalTrigger(seconds=config.REVIEW_INTERVAL),
            id='review_task',
            name='审核任务',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info(f"  ✓ 审核任务 (间隔: {config.REVIEW_INTERVAL}秒)")

        # 3. 补件检查 - 每30分钟执行
        self.scheduler.add_job(
            self._run_supplementary_check,
            trigger=IntervalTrigger(seconds=config.SUPPLEMENTARY_CHECK_INTERVAL),
            id='supplementary_check',
            name='补件检查',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info(f"  ✓ 补件检查任务 (间隔: {config.SUPPLEMENTARY_CHECK_INTERVAL}秒)")

        # 4. 清理任务 - 每天凌晨2点执行
        self.scheduler.add_job(
            self._run_cleanup,
            trigger=CronTrigger(hour=2, minute=0),
            id='cleanup_task',
            name='清理任务',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info("  ✓ 清理任务 (每天凌晨2点)")

        # 6. 案件文件清理 - 每周一凌晨3点执行
        self.scheduler.add_job(
            self._run_claims_cleanup,
            trigger=CronTrigger(day_of_week='mon', hour=3, minute=0),
            id='claims_cleanup',
            name='案件文件清理',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info("  ✓ 案件文件清理任务 (每周一凌晨3点，删除7天前的案件)")

        # 7. 孤儿案件全量兜底审核 - 每周日03:05执行
        self.scheduler.add_job(
            self._run_orphan_sweep_review,
            trigger=CronTrigger(day_of_week='sun', hour=3, minute=5),
            id='orphan_sweep_review',
            name='孤儿案件全量兜底审核',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info("  ✓ 孤儿案件全量兜底审核任务 (每周日03:05，审核所有本地未审核案件)")

        # 5. 健康检查 - 每5分钟执行
        self.scheduler.add_job(
            self._run_health_check,
            trigger=IntervalTrigger(minutes=5),
            id='health_check',
            name='健康检查',
            max_instances=1,
            replace_existing=True
        )
        LOGGER.info("  ✓ 健康检查任务 (每5分钟)")

    async def _run_hourly_check(self):
        """执行每小时检查"""
        try:
            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("定时任务: 每小时检查")
            LOGGER.info("=" * 60)

            result = await self.workflow.run_hourly_check()

            LOGGER.info(f"每小时检查完成: {result.get('status')}")
            if result.get('summary'):
                LOGGER.info(f"汇总: {result['summary']}")

        except Exception as e:
            LOGGER.error(f"每小时检查任务异常: {e}", exc_info=True)

    async def _run_review(self):
        """执行审核任务"""
        try:
            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("定时任务: 审核待处理案件")
            LOGGER.info("=" * 60)

            count, message = await self.workflow.review_scheduler.process_pending_reviews()

            LOGGER.info(f"审核任务完成: {message}")

        except Exception as e:
            LOGGER.error(f"审核任务异常: {e}", exc_info=True)

    async def _run_supplementary_check(self):
        """执行补件检查"""
        try:
            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("定时任务: 补件检查")
            LOGGER.info("=" * 60)

            result = await self.workflow.supplementary_handler.check_supplementary_deadline()
            LOGGER.info(f"补件提醒: {result[1]}")

            result = await self.workflow.supplementary_handler.check_supplementary_timeout()
            LOGGER.info(f"补件超时: {result[1]}")

            result = await self.workflow.supplementary_handler.check_supplementary_received()
            LOGGER.info(f"补件接收: {result[1]}")

        except Exception as e:
            LOGGER.error(f"补件检查任务异常: {e}", exc_info=True)

    async def _run_cleanup(self):
        """执行清理任务"""
        try:
            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("定时任务: 数据清理")
            LOGGER.info("=" * 60)

            result = await self.workflow.run_cleanup(days_to_keep=30)

            LOGGER.info(f"清理任务完成: {result.get('status')}")

        except Exception as e:
            LOGGER.error(f"清理任务异常: {e}", exc_info=True)

    async def _run_claims_cleanup(self):
        """清理7天前的案件文件"""
        import shutil
        from datetime import timedelta
        from pathlib import Path

        try:
            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("定时任务: 案件文件清理（删除7天前）")
            LOGGER.info("=" * 60)

            claims_dir = config.CLAIMS_DATA_DIR
            cutoff = datetime.now() - timedelta(days=7)
            deleted = 0
            skipped = 0

            for folder in claims_dir.rglob("claim_info.json"):
                claim_folder = folder.parent
                # 以文件夹最后修改时间判断
                mtime = datetime.fromtimestamp(claim_folder.stat().st_mtime)
                if mtime < cutoff:
                    try:
                        shutil.rmtree(claim_folder)
                        deleted += 1
                        LOGGER.info(f"  已删除: {claim_folder.name} (修改时间: {mtime.strftime('%Y-%m-%d')})")
                    except Exception as e:
                        skipped += 1
                        LOGGER.warning(f"  删除失败: {claim_folder.name}: {e}")

            LOGGER.info(f"案件文件清理完成: 删除 {deleted} 个，失败 {skipped} 个")

        except Exception as e:
            LOGGER.error(f"案件文件清理异常: {e}", exc_info=True)

    async def _run_orphan_sweep_review(self):
        """执行孤儿案件全量兜底审核（每周日03:05）"""
        import json as _json
        import aiohttp as _aiohttp

        try:
            LOGGER.info("\n" + "=" * 60)
            LOGGER.info("定时任务: 孤儿案件全量兜底审核")
            LOGGER.info("=" * 60)

            registered = 0
            reviewed = 0
            skipped = 0
            failed = 0

            CONCLUDED_STATUSES = {
                "零结关案", "支付成功", "事后理赔拒赔",
                "取消理赔", "结案待财务付款",
            }

            # 获取已审核 forceid
            reviewed_ids = set()
            for f in config.REVIEW_RESULTS_DIR.rglob("*_ai_review.json"):
                reviewed_ids.add(f.stem.replace("_ai_review", ""))

            # 注册所有孤儿案件
            orphan_forceids = []
            for info_file in config.CLAIMS_DATA_DIR.rglob("claim_info.json"):
                try:
                    data = _json.loads(info_file.read_text(encoding="utf-8"))
                    forceid = str(data.get("forceid") or "").strip()
                    if not forceid or forceid in reviewed_ids:
                        skipped += 1
                        continue
                    final_status = str(data.get("Final_Status") or "").strip()
                    if final_status in CONCLUDED_STATUSES:
                        skipped += 1
                        continue

                    existing = await self.workflow.status_manager.get_claim_status(forceid)
                    if existing is not None:
                        skipped += 1
                        continue

                    benefit = str(data.get("BenefitName") or "")
                    claim_type = _detect_claim_type(benefit)
                    claim_id = data.get("ClaimId") or forceid

                    await self.workflow.status_manager.create_claim_status(
                        claim_id=claim_id,
                        forceid=forceid,
                        claim_type=claim_type,
                        initial_status="downloaded",
                    )
                    orphan_forceids.append((forceid, claim_type))
                    registered += 1
                except Exception as e:
                    LOGGER.warning(f"孤儿注册异常 {info_file}: {e}")

            LOGGER.info(f"兜底注册完成: {registered} 个待审，跳过 {skipped} 个")

            if not orphan_forceids:
                LOGGER.info("没有需要兜底审核的案件")
                return

            # 对孤儿案件执行 AI 审核
            from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
            from app.policy_terms_registry import POLICY_TERMS
            from app.output.frontend_pusher import push_to_frontend

            reviewer = AIClaimReviewer()
            terms_cache = {}

            connector = _aiohttp.TCPConnector()
            async with _aiohttp.ClientSession(connector=connector, trust_env=True) as session:
                for i, (forceid, claim_type) in enumerate(orphan_forceids, 1):
                    LOGGER.info(f"  [{i}/{len(orphan_forceids)}] 兜底审核: {forceid}")

                    try:
                        # 找案件目录
                        claim_folder = None
                        for info_file in config.CLAIMS_DATA_DIR.rglob("claim_info.json"):
                            try:
                                d = _json.loads(info_file.read_text(encoding="utf-8"))
                                if str(d.get("forceid") or "") == forceid:
                                    claim_folder = info_file.parent
                                    break
                            except:
                                continue

                        if not claim_folder:
                            LOGGER.warning(f"  找不到案件目录: {forceid}")
                            failed += 1
                            continue

                        # 加载条款
                        if claim_type not in terms_cache:
                            try:
                                tf = POLICY_TERMS.resolve(claim_type)
                                terms_cache[claim_type] = tf.read_text(encoding="utf-8")
                            except:
                                terms_cache[claim_type] = ""

                        result = await review_claim_async(
                            reviewer, claim_folder, terms_cache[claim_type],
                            i, len(orphan_forceids), session
                        )

                        if not result:
                            failed += 1
                            continue

                        # 保存审核结果
                        output_dir = config.REVIEW_RESULTS_DIR / claim_type
                        output_dir.mkdir(parents=True, exist_ok=True)
                        rf = output_dir / f"{result['forceid']}_ai_review.json"
                        rf.write_text(_json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                        LOGGER.info(f"  审核结果已保存: {rf.name}")
                        LOGGER.info(f"  audit_result: {result.get('flight_delay_audit', {}).get('audit_result', '')}")

                        # 推送前端
                        push_result = await push_to_frontend(result, session)
                        if push_result.get("success"):
                            LOGGER.info(f"  推送成功: {forceid}")
                        else:
                            LOGGER.warning(f"  推送失败: {forceid}")

                        reviewed += 1

                    except Exception as e:
                        LOGGER.warning(f"  兜底审核异常 {forceid}: {e}")
                        failed += 1

            LOGGER.info(f"孤儿案件全量兜底审核完成: 注册 {registered}，审核 {reviewed}，失败 {failed}，跳过 {skipped}")

        except Exception as e:
            LOGGER.error(f"孤儿案件全量兜底审核异常: {e}", exc_info=True)

    async def _run_health_check(self):
        """执行健康检查"""
        try:
            status = await self.workflow.get_system_status()

            # 检查关键组件
            if status.get("is_running"):
                LOGGER.info("系统状态: 正在运行")
            else:
                LOGGER.debug("系统状态: 空闲")

        except Exception as e:
            LOGGER.error(f"健康检查异常: {e}", exc_info=True)

    def _job_executed(self, event):
        """任务执行成功回调"""
        job_id = event.job_id
        LOGGER.info(f"任务执行成功: {job_id}")

    def _job_error(self, event):
        """任务执行失败回调"""
        job_id = event.job_id
        exception = event.exception
        LOGGER.error(f"任务执行失败: {job_id}, 异常: {exception}")

    def start(self):
        """启动调度器"""
        if not self.is_initialized:
            LOGGER.error("调度器未初始化，请先调用 initialize()")
            return

        LOGGER.info("启动定时任务调度器...")
        self.scheduler.start()
        LOGGER.info("✓ 定时任务调度器已启动")

        # 打印所有任务
        self._print_jobs()

    def stop(self):
        """停止调度器"""
        LOGGER.info("停止定时任务调度器...")
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        LOGGER.info("✓ 定时任务调度器已停止")

    def _print_jobs(self):
        """打印所有任务"""
        jobs = self.scheduler.get_jobs()

        LOGGER.info("\n当前定时任务:")
        LOGGER.info("-" * 80)
        for job in jobs:
            next_run = job.next_run_time
            LOGGER.info(f"  {job.name:20s} | ID: {job.id:20s} | 下次执行: {next_run}")
        LOGGER.info("-" * 80)

    def add_manual_job(self, job_func, trigger, job_id: str, **kwargs):
        """
        添加手动任务

        Args:
            job_func: 任务函数
            trigger: 触发器
            job_id: 任务ID
            **kwargs: 其他参数
        """
        self.scheduler.add_job(
            job_func,
            trigger=trigger,
            id=job_id,
            **kwargs
        )
        LOGGER.info(f"添加手动任务: {job_id}")

    def remove_job(self, job_id: str):
        """
        移除任务

        Args:
            job_id: 任务ID
        """
        self.scheduler.remove_job(job_id)
        LOGGER.info(f"移除任务: {job_id}")

    def pause_job(self, job_id: str):
        """暂停任务"""
        self.scheduler.pause_job(job_id)
        LOGGER.info(f"暂停任务: {job_id}")

    def resume_job(self, job_id: str):
        """恢复任务"""
        self.scheduler.resume_job(job_id)
        LOGGER.info(f"恢复任务: {job_id}")


# 全局实例
_task_scheduler = None


def get_task_scheduler() -> TaskScheduler:
    """获取定时任务调度器实例"""
    global _task_scheduler
    if _task_scheduler is None:
        _task_scheduler = TaskScheduler()
    return _task_scheduler


async def main():
    """主函数"""
    import signal

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('scheduler.log', encoding='utf-8', delay=False)
        ]
    )

    LOGGER.info("=" * 80)
    LOGGER.info("航班延误AI审核系统 - 定时任务调度器")
    LOGGER.info("=" * 80)

    # 创建调度器
    scheduler = get_task_scheduler()

    try:
        # 初始化
        await scheduler.initialize()

        # 启动
        scheduler.start()

        LOGGER.info("\n系统已启动，按 Ctrl+C 停止...")

        # 保持运行
        stop_event = asyncio.Event()

        def signal_handler(sig, frame):
            LOGGER.info("\n收到停止信号...")
            stop_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # 等待停止信号
        await stop_event.wait()

    except Exception as e:
        LOGGER.error(f"调度器异常: {e}", exc_info=True)
    finally:
        scheduler.stop()
        await scheduler.workflow.shutdown()
        LOGGER.info("系统已停止")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断")