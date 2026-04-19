#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产环境启动脚本
启动定时任务调度器
"""

import os
import sys
import logging
import asyncio
from pathlib import Path
from datetime import datetime

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.scheduler.task_scheduler import get_task_scheduler


def setup_logging():
    """配置日志"""
    # 创建日志目录
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)

    # 日志文件名包含日期
    log_file = log_dir / f"production_{datetime.now().strftime('%Y%m%d')}.log"

    # 避免重复配置
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return logging.getLogger(__name__)

    # 配置日志格式
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            # 控制台输出
            logging.StreamHandler(sys.stdout),
            # 文件输出（delay=False 确保每次写入后立即刷盘，避免日志中断）
            logging.FileHandler(log_file, encoding='utf-8', delay=False),
        ]
    )

    # 禁用特定logger的传播，避免日志重复输出
    # 这些模块会配置自己的handler，不需要向root传播
    for logger_name in ['scripts.download_claims', 'scripts']:
        logger = logging.getLogger(logger_name)
        logger.propagate = False

    # 设置第三方库日志级别
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('aiomysql').setLevel(logging.WARNING)

    return logging.getLogger(__name__)


def check_environment():
    """检查环境配置"""
    logger = logging.getLogger(__name__)

    # 检查.env文件
    env_file = project_root / '.env'
    if not env_file.exists():
        logger.error(".env 文件不存在，请复制 .env.production 并配置")
        return False

    # 检查必需的环境变量
    required_vars = [
        'DASHSCOPE_API_KEY',
        'DB_HOST',
        'DB_USER',
        'DB_PASSWORD',
        'DB_NAME'
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        logger.error(f"缺少必需的环境变量: {', '.join(missing_vars)}")
        return False

    logger.info("✓ 环境检查通过")
    return True


async def main():
    """主函数"""
    # 配置日志
    logger = setup_logging()

    logger.info("=" * 80)
    logger.info("航班延误AI审核系统 - 生产环境启动")
    logger.info("=" * 80)
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    # 检查环境
    if not check_environment():
        logger.error("环境检查失败，请检查配置")
        sys.exit(1)

    # 创建调度器
    scheduler = get_task_scheduler()

    try:
        # 初始化
        logger.info("初始化系统...")
        await scheduler.initialize()

        # 启动
        logger.info("启动定时任务调度器...")
        scheduler.start()

        # 启动后立即执行一次增量审核（不等待下一个周期）
        logger.info("立即触发一次增量审核...")
        asyncio.create_task(scheduler.workflow.run_hourly_check())

        logger.info("\n" + "=" * 80)
        logger.info("系统已启动，按 Ctrl+C 停止")
        logger.info("=" * 80)

        # 保持运行
        stop_event = asyncio.Event()

        def signal_handler(sig, frame):
            logger.info("\n收到停止信号...")
            stop_event.set()

        import signal
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # 等待停止信号
        await stop_event.wait()

    except Exception as e:
        logger.error(f"系统异常: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("关闭系统...")
        scheduler.stop()
        await scheduler.workflow.shutdown()
        logger.info("系统已停止")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n用户中断")