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
            logging.FileHandler('scheduler.log', encoding='utf-8')
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