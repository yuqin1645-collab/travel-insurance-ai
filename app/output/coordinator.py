#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
输出协调器
同时将审核结果推送到前端和写入数据库
"""

import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum

import aiohttp

from app.config import config
from app.state.status_manager import get_status_manager
from app.db.models import ReviewResult
from app.db.database import get_review_result_dao, get_db_connection
from app.output.frontend_pusher import build_api_payload, push_to_frontend

LOGGER = logging.getLogger(__name__)


class OutputType(str, Enum):
    """输出类型"""
    FRONTEND = "frontend"
    DATABASE = "database"
    BOTH = "both"


class OutputStatus(str, Enum):
    """输出状态"""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
    PENDING = "pending"


class OutputCoordinator:
    """输出协调器"""

    def __init__(
        self,
        frontend_url: Optional[str] = None,
        frontend_api_key: Optional[str] = None,
        frontend_timeout: Optional[int] = None
    ):
        self.frontend_url = frontend_url or config.FRONTEND_API_URL
        self.frontend_api_key = frontend_api_key or config.FRONTEND_API_KEY
        self.frontend_timeout = frontend_timeout or config.FRONTEND_TIMEOUT
        self.db = get_db_connection()
        self.review_result_dao = get_review_result_dao()
        self.status_manager = get_status_manager()

        # 重试配置
        self.max_retries = 3
        self.retry_delay = 5  # 秒

    async def initialize(self):
        """初始化"""
        await self.db.initialize()
        LOGGER.info("输出协调器初始化完成")

    async def dispatch_output(
        self,
        forceid: str,
        review_result: Dict[str, Any],
        output_type: OutputType = OutputType.BOTH
    ) -> Dict[str, Any]:
        """
        分发审核结果输出

        Args:
            forceid: 案件唯一ID
            review_result: 审核结果
            output_type: 输出类型

        Returns:
            输出结果详情
        """
        LOGGER.info(f"分发审核结果: {forceid}, 类型: {output_type}")

        results = {
            "forceid": forceid,
            "timestamp": datetime.now().isoformat(),
            "outputs": {}
        }

        # 1. 保存到数据库
        if output_type in [OutputType.DATABASE, OutputType.BOTH]:
            db_success, db_message = await self._save_to_database(forceid, review_result)
            results["outputs"]["database"] = {
                "status": OutputStatus.SUCCESS if db_success else OutputStatus.FAILED,
                "message": db_message
            }

        # 2. 推送到前端
        if output_type in [OutputType.FRONTEND, OutputType.BOTH]:
            frontend_success, frontend_message = await self._push_to_frontend(forceid, review_result)
            results["outputs"]["frontend"] = {
                "status": OutputStatus.SUCCESS if frontend_success else OutputStatus.FAILED,
                "message": frontend_message
            }

        # 3. 确定整体状态
        outputs = results["outputs"]
        if all(v["status"] == OutputStatus.SUCCESS for v in outputs.values()):
            results["overall_status"] = OutputStatus.SUCCESS
        elif all(v["status"] == OutputStatus.FAILED for v in outputs.values()):
            results["overall_status"] = OutputStatus.FAILED
        else:
            results["overall_status"] = OutputStatus.PARTIAL

        LOGGER.info(f"分发完成: {forceid}, 状态: {results['overall_status']}")
        return results

    async def _save_to_database(
        self,
        forceid: str,
        review_result: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        保存到数据库

        Args:
            forceid: 案件唯一ID
            review_result: 审核结果

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"保存审核结果到数据库: {forceid}")

        try:
            # 构建审核结果记录
            result_record = ReviewResult(
                forceid=forceid,
                claim_id=review_result.get("claim_id"),
                remark=review_result.get("Remark", ""),
                is_additional=review_result.get("IsAdditional", "N"),
                key_conclusions=str(review_result.get("KeyConclusions", [])),
                raw_result=str(review_result),
                review_status="completed",
                final_decision=review_result.get("final_decision"),
                decision_reason=review_result.get("decision_reason"),
                forwarded_to_frontend=False,
                metadata={
                    "review_timestamp": datetime.now().isoformat(),
                    "source": "ai_review",
                    "version": "2.0"
                }
            )

            # 保存到数据库
            await self.review_result_dao.create_or_update_result(result_record)

            LOGGER.info(f"✓ 保存数据库成功: {forceid}")
            return True, "保存成功"

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"✗ 保存数据库失败: {forceid}, 错误: {error_msg}")
            return False, f"保存失败: {error_msg}"

    async def _push_to_frontend(
        self,
        forceid: str,
        review_result: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        推送到前端

        Args:
            forceid: 案件唯一ID
            review_result: 审核结果

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"推送审核结果到前端: {forceid}")

        # 重试机制
        for attempt in range(1, self.max_retries + 1):
            try:
                connector = aiohttp.TCPConnector()
                async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
                    response = await push_to_frontend(review_result, session)

                if response.get("success"):
                    # 更新数据库中的推送状态
                    await self.review_result_dao.update_frontend_status(
                        forceid,
                        forwarded=True,
                        response=response.get("response", "")
                    )

                    LOGGER.info(f"✓ 推送前端成功: {forceid}")
                    return True, "推送成功"

                else:
                    error_msg = response.get("response", "未知错误")
                    LOGGER.warning(f"推送前端失败: {forceid}, 尝试 {attempt}/{self.max_retries}, 错误: {error_msg}")

                    if attempt < self.max_retries:
                        await asyncio.sleep(self.retry_delay)
                        continue

                    return False, f"推送失败: {error_msg}"

            except Exception as e:
                error_msg = str(e)
                LOGGER.warning(f"推送前端异常: {forceid}, 尝试 {attempt}/{self.max_retries}, 错误: {error_msg}")

                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                    continue

                return False, f"推送异常: {error_msg}"

        return False, "超过最大重试次数"

    async def retry_failed_outputs(self, limit: int = 10) -> Dict[str, int]:
        """
        重试失败的输出

        Args:
            limit: 限制数量

        Returns:
            重试结果统计
        """
        LOGGER.info(f"重试失败的输出 (限制: {limit})")

        results = {
            "total": 0,
            "database_success": 0,
            "frontend_success": 0,
            "both_success": 0,
            "failed": 0
        }

        try:
            # 获取需要重试的记录
            # TODO: 实现从数据库查询未成功推送的记录

            # 示例：假设有一个列表
            failed_records = []  # await self._get_failed_records(limit)

            results["total"] = len(failed_records)

            for record in failed_records:
                forceid = record.get("forceid")
                review_result = record.get("review_result", {})

                # 重试输出
                output_result = await self.dispatch_output(forceid, review_result)

                if output_result["overall_status"] == OutputStatus.SUCCESS:
                    results["both_success"] += 1
                elif OutputStatus.SUCCESS in [v["status"] for v in output_result["outputs"].values()]:
                    results["frontend_success"] += 1
                else:
                    results["failed"] += 1

            LOGGER.info(f"重试完成: {results}")
            return results

        except Exception as e:
            LOGGER.error(f"重试失败输出异常: {e}")
            return results

    async def batch_dispatch(
        self,
        results: List[Tuple[str, Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """
        批量分发审核结果

        Args:
            results: [(forceid, review_result), ...]

        Returns:
            批量处理结果
        """
        LOGGER.info(f"批量分发审核结果: {len(results)} 个案件")

        batch_results = {
            "total": len(results),
            "success": 0,
            "partial": 0,
            "failed": 0,
            "details": []
        }

        for forceid, review_result in results:
            try:
                result = await self.dispatch_output(forceid, review_result)

                if result["overall_status"] == OutputStatus.SUCCESS:
                    batch_results["success"] += 1
                elif result["overall_status"] == OutputStatus.PARTIAL:
                    batch_results["partial"] += 1
                else:
                    batch_results["failed"] += 1

                batch_results["details"].append({
                    "forceid": forceid,
                    "status": result["overall_status"],
                    "outputs": result["outputs"]
                })

            except Exception as e:
                batch_results["failed"] += 1
                batch_results["details"].append({
                    "forceid": forceid,
                    "status": OutputStatus.FAILED,
                    "error": str(e)
                })
                LOGGER.error(f"批量分发异常: {forceid}, 错误: {e}")

        LOGGER.info(f"批量分发完成: 成功 {batch_results['success']}, 部分成功 {batch_results['partial']}, 失败 {batch_results['failed']}")
        return batch_results


# 全局实例
_output_coordinator = None


def get_output_coordinator() -> OutputCoordinator:
    """获取输出协调器实例"""
    global _output_coordinator
    if _output_coordinator is None:
        _output_coordinator = OutputCoordinator()
    return _output_coordinator


async def run_output_dispatch(forceid: str, review_result: Dict[str, Any]):
    """
    运行输出分发（用于定时任务或回调）

    Args:
        forceid: 案件唯一ID
        review_result: 审核结果
    """
    coordinator = get_output_coordinator()
    await coordinator.initialize()

    try:
        result = await coordinator.dispatch_output(forceid, review_result)
        return result
    finally:
        await coordinator.db.close()


if __name__ == '__main__':
    # 测试
    import asyncio

    async def test():
        coordinator = OutputCoordinator()
        await coordinator.initialize()

        try:
            # 示例审核结果
            test_result = {
                "forceid": "test_001",
                "Remark": "测试审核结果",
                "IsAdditional": "N",
                "KeyConclusions": [],
                "flight_delay_audit": {
                    "audit_result": "通过",
                    "confidence_score": 0.95,
                    "payout_suggestion": {
                        "currency": "CNY",
                        "amount": 300,
                        "basis": "延误5小时"
                    },
                    "key_data": {
                        "passenger_name": "张三",
                        "delay_duration_minutes": 300,
                        "reason": "航班取消"
                    },
                    "logic_check": {},
                    "explanation": "测试说明"
                }
            }

            result = await coordinator.dispatch_output("test_001", test_result)
            print(f"结果: {result}")

        finally:
            await coordinator.db.close()

    asyncio.run(test())