#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
健康检查端点
提供系统健康状态接口
"""

import os
import shutil
import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass

from app.config import config
from app.db.database import get_db_connection
from app.db.models import TaskStatus

LOGGER = logging.getLogger(__name__)


@dataclass
class HealthCheck:
    """健康检查结果"""
    name: str
    status: str  # "healthy" | "unhealthy" | "degraded"
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self.db = get_db_connection()
        self.last_check_time: Optional[datetime] = None
        self.last_check_result: Optional[Dict[str, Any]] = None

    async def initialize(self):
        """初始化"""
        await self.db.initialize()
        LOGGER.info("健康检查器初始化完成")

    async def check_health(self) -> Dict[str, Any]:
        """
        执行全面健康检查

        Returns:
            健康检查结果
        """
        LOGGER.debug("执行健康检查...")

        checks = {
            "database": await self._check_database(),
            "api_connectivity": await self._check_api_connectivity(),
            "disk_space": await self._check_disk_space(),
            "memory_usage": await self._check_memory_usage(),
            "scheduler": await self._check_scheduler(),
            "tasks": await self._check_recent_tasks(),
        }

        # 判断整体状态
        unhealthy_checks = [k for k, v in checks.items() if v["status"] == "unhealthy"]
        degraded_checks = [k for k, v in checks.items() if v["status"] == "degraded"]

        if unhealthy_checks:
            overall_status = "unhealthy"
        elif degraded_checks:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        result = {
            "status": overall_status,
            "timestamp": datetime.now().isoformat(),
            "checks": checks,
            "unhealthy_count": len(unhealthy_checks),
            "degraded_count": len(degraded_checks)
        }

        self.last_check_time = datetime.now()
        self.last_check_result = result

        return result

    async def _check_database(self) -> HealthCheck:
        """检查数据库连接"""
        try:
            async with self.db.get_connection() as conn:
                async with conn.cursor() as cursor:
                    # 执行简单查询
                    await cursor.execute("SELECT 1")

                    # 检查连接池状态
                    pool = self.db.pool
                    pool_size = pool.size if pool else 0
                    pool_free = pool.freesize if pool else 0

                    details = {
                        "host": config.DB_HOST,
                        "port": config.DB_PORT,
                        "database": config.DB_NAME,
                        "pool_size": pool_size,
                        "pool_free": pool_free
                    }

                    # 如果连接池快满了，标记为降级
                    if pool_free < 2:
                        return HealthCheck(
                            name="database",
                            status="degraded",
                            message=f"数据库连接池剩余 {pool_free} 个连接",
                            details=details
                        )

                    return HealthCheck(
                        name="database",
                        status="healthy",
                        message="数据库连接正常",
                        details=details
                    )

        except Exception as e:
            return HealthCheck(
                name="database",
                status="unhealthy",
                message=f"数据库连接失败: {e}",
                details={"error": str(e)}
            )

    async def _check_api_connectivity(self) -> HealthCheck:
        """检查API连接"""
        # TODO: 检查外部API连通性
        # - 飞常准API
        # - 上游案件API
        # - 前端推送API

        return HealthCheck(
            name="api_connectivity",
            status="healthy",
            message="API连接检查跳过（待实现）"
        )

    async def _check_disk_space(self) -> HealthCheck:
        """检查磁盘空间"""
        try:
            # 检查项目目录所在磁盘
            usage = shutil.disk_usage(project_root)

            free_gb = usage.free / (1024 ** 3)
            percent_used = (usage.used / usage.total) * 100

            details = {
                "free_gb": round(free_gb, 2),
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "percent_used": round(percent_used, 1)
            }

            if free_gb < 1:
                return HealthCheck(
                    name="disk_space",
                    status="unhealthy",
                    message=f"磁盘空间不足: {free_gb:.1f}GB",
                    details=details
                )
            elif free_gb < 5:
                return HealthCheck(
                    name="disk_space",
                    status="degraded",
                    message=f"磁盘空间较低: {free_gb:.1f}GB",
                    details=details
                )
            else:
                return HealthCheck(
                    name="disk_space",
                    status="healthy",
                    message=f"磁盘空间充足: {free_gb:.1f}GB",
                    details=details
                )

        except Exception as e:
            return HealthCheck(
                name="disk_space",
                status="unhealthy",
                message=f"磁盘空间检查失败: {e}",
                details={"error": str(e)}
            )

    async def _check_memory_usage(self) -> HealthCheck:
        """检查内存使用"""
        try:
            import psutil

            memory = psutil.virtual_memory()
            available_gb = memory.available / (1024 ** 3)
            percent_used = memory.percent

            details = {
                "available_gb": round(available_gb, 2),
                "total_gb": round(memory.total / (1024 ** 3), 2),
                "percent_used": round(percent_used, 1)
            }

            if percent_used > 90:
                return HealthCheck(
                    name="memory_usage",
                    status="unhealthy",
                    message=f"内存使用率过高: {percent_used:.1f}%",
                    details=details
                )
            elif percent_used > 80:
                return HealthCheck(
                    name="memory_usage",
                    status="degraded",
                    message=f"内存使用率较高: {percent_used:.1f}%",
                    details=details
                )
            else:
                return HealthCheck(
                    name="memory_usage",
                    status="healthy",
                    message=f"内存使用正常: {percent_used:.1f}%",
                    details=details
                )

        except ImportError:
            return HealthCheck(
                name="memory_usage",
                status="healthy",
                message="psutil未安装，跳过内存检查"
            )
        except Exception as e:
            return HealthCheck(
                name="memory_usage",
                status="unhealthy",
                message=f"内存检查失败: {e}",
                details={"error": str(e)}
            )

    async def _check_scheduler(self) -> HealthCheck:
        """检查定时任务调度器"""
        # TODO: 检查调度器是否正在运行

        return HealthCheck(
            name="scheduler",
            status="healthy",
            message="调度器检查跳过（待实现）"
        )

    async def _check_recent_tasks(self) -> HealthCheck:
        """检查最近任务执行情况"""
        try:
            # 获取最近的任务日志
            logs = await self.db.scheduler_log_dao.get_recent_logs(limit=100)

            if not logs:
                return HealthCheck(
                    name="tasks",
                    status="healthy",
                    message="暂无任务记录"
                )

            # 统计
            success_count = sum(1 for log in logs if log.status == TaskStatus.SUCCESS)
            failed_count = sum(1 for log in logs if log.status == TaskStatus.FAILED)
            total_count = len(logs)
            success_rate = success_count / total_count if total_count > 0 else 0

            details = {
                "total": total_count,
                "success": success_count,
                "failed": failed_count,
                "success_rate": round(success_rate * 100, 1)
            }

            if success_rate < 0.8:
                return HealthCheck(
                    name="tasks",
                    status="degraded",
                    message=f"任务成功率较低: {success_rate*100:.1f}%",
                    details=details
                )
            else:
                return HealthCheck(
                    name="tasks",
                    status="healthy",
                    message=f"任务执行正常，成功率: {success_rate*100:.1f}%",
                    details=details
                )

        except Exception as e:
            return HealthCheck(
                name="tasks",
                status="unhealthy",
                message=f"任务检查失败: {e}",
                details={"error": str(e)}
            )

    def get_health_status(self) -> Dict[str, Any]:
        """
        获取健康状态（快速返回，不执行检查）

        Returns:
            健康状态
        """
        if self.last_check_result:
            return {
                "status": self.last_check_result["status"],
                "timestamp": self.last_check_result["timestamp"],
                "cached": True
            }
        else:
            return {
                "status": "unknown",
                "timestamp": None,
                "cached": False
            }


# 全局实例
_health_checker = None


def get_health_checker() -> HealthChecker:
    """获取健康检查器实例"""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker()
    return _health_checker


async def run_health_check():
    """运行健康检查"""
    checker = get_health_checker()
    await checker.initialize()

    try:
        result = await checker.check_health()
        return result
    finally:
        await checker.db.close()


if __name__ == '__main__':

    async def test():
        result = await run_health_check()
        print(f"健康检查结果: {result['status']}")
        print("\n各检查项:")
        for name, check in result['checks'].items():
            print(f"  {name}: {check['status']} - {check['message']}")

    asyncio.run(test())