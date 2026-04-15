#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补件处理器
处理AI返回"需补件"的案件
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from app.config import config
from app.state.status_manager import get_status_manager, StatusManager
from app.state.constants import ClaimStatus, SupplementaryStatus
from app.db.models import SupplementaryRecord
from app.db.database import get_supplementary_dao, get_db_connection

LOGGER = logging.getLogger(__name__)


class SupplementaryHandler:
    """补件处理器"""

    def __init__(
        self,
        status_manager: Optional[StatusManager] = None
    ):
        self.status_manager = status_manager or get_status_manager()
        self.supplementary_dao = get_supplementary_dao()
        self.db = get_db_connection()

        # 配置
        self.deadline_hours = config.SUPPLEMENTARY_DEADLINE_HOURS
        self.max_supplementary_count = config.MAX_SUPPLEMENTARY_COUNT
        self.reminder_hours = config.SUPPLEMENTARY_REMINDER_HOURS

    async def initialize(self):
        """初始化"""
        await self.db.initialize()
        LOGGER.info("补件处理器初始化完成")

    async def handle_supplementary_needed(
        self,
        forceid: str,
        review_result: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        处理"需补件"结果

        Args:
            forceid: 案件唯一ID
            review_result: AI审核结果

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"处理需补件案件: {forceid}")

        # 1. 获取当前案件状态
        status_record = await self.status_manager.claim_status_dao.get_status_by_forceid(forceid)
        if not status_record:
            return False, f"案件不存在: {forceid}"

        current_count = status_record.supplementary_count

        # 2. 检查补件次数
        if current_count >= self.max_supplementary_count:
            LOGGER.warning(f"案件 {forceid} 已超过最大补件次数 ({current_count}/{self.max_supplementary_count})")

            # 超过次数，直接拒绝
            await self._reject_claim(forceid, "超过最大补件次数")
            return False, "超过最大补件次数，已拒绝"

        # 3. 计算截止时间
        deadline = datetime.now() + timedelta(hours=self.deadline_hours)

        # 4. 构建补件原因
        supplementary_reason = review_result.get("Remark", "需补件")
        missing_materials = self._extract_missing_materials(review_result)

        # 5. 创建补件记录
        supplementary_record = SupplementaryRecord(
            claim_id=status_record.claim_id,
            forceid=forceid,
            supplementary_number=current_count + 1,
            requested_at=datetime.now(),
            requested_reason=supplementary_reason,
            required_materials=missing_materials,
            deadline=deadline,
            status=SupplementaryStatus.REQUESTED
        )

        try:
            record_id = await self.supplementary_dao.create_supplementary_record(supplementary_record)
            LOGGER.info(f"创建补件记录成功: {forceid}, 记录ID: {record_id}")

            # 6. 更新案件状态
            await self.status_manager.update_claim_status(
                forceid,
                ClaimStatus.PENDING_SUPPLEMENTARY,
                f"第{current_count + 1}次补件请求"
            )

            # 7. 通知前端（TODO: 调用前端API）
            await self._notify_frontend_supplementary(forceid, supplementary_record)

            message = f"补件请求已创建: 第{current_count + 1}次，截止时间 {deadline.strftime('%Y-%m-%d %H:%M')}"
            LOGGER.info(message)
            return True, message

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"处理需补件失败: {forceid}, 错误: {error_msg}")
            return False, f"处理失败: {error_msg}"

    async def check_supplementary_deadline(self) -> Tuple[int, str]:
        """
        检查补件截止时间，发送提醒

        Returns:
            (提醒数量, 消息)
        """
        LOGGER.info("检查补件截止时间...")

        reminder_count = 0

        try:
            # 获取即将到期的补件（提前6小时提醒）
            expiring_records = await self.supplementary_dao.get_expiring_supplementary(
                hours_before=self.reminder_hours
            )

            for record in expiring_records:
                forceid = record.forceid

                try:
                    # 发送提醒
                    await self._send_reminder(forceid, record)
                    reminder_count += 1

                except Exception as e:
                    LOGGER.error(f"发送补件提醒失败: {forceid}, 错误: {e}")

            message = f"检查完成: 发送 {reminder_count} 个提醒"
            LOGGER.info(message)
            return reminder_count, message

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"检查补件截止时间失败: {error_msg}")
            return 0, f"检查失败: {error_msg}"

    async def check_supplementary_timeout(self) -> Tuple[int, str]:
        """
        检查补件超时，处理超时案件

        Returns:
            (超时处理数量, 消息)
        """
        LOGGER.info("检查补件超时...")

        timeout_count = 0

        try:
            # 获取超时的补件
            pending_records = await self.supplementary_dao.get_pending_supplementary(limit=100)

            now = datetime.now()
            for record in pending_records:
                if record.deadline and record.deadline < now:
                    forceid = record.forceid

                    try:
                        # 处理超时
                        await self._handle_supplementary_timeout(forceid, record)
                        timeout_count += 1

                    except Exception as e:
                        LOGGER.error(f"处理补件超时失败: {forceid}, 错误: {e}")

            message = f"检查完成: 处理 {timeout_count} 个超时补件"
            LOGGER.info(message)
            return timeout_count, message

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"检查补件超时失败: {error_msg}")
            return 0, f"检查失败: {error_msg}"

    async def check_supplementary_received(self) -> Tuple[int, str]:
        """
        检查是否收到补件材料

        Returns:
            (收到补件数量, 消息)
        """
        LOGGER.info("检查补件接收状态...")

        received_count = 0

        try:
            # 获取待确认的补件
            pending_records = await self.supplementary_dao.get_pending_supplementary(limit=100)

            for record in pending_records:
                forceid = record.forceid

                try:
                    # 检查是否有新上传的材料
                    has_new_materials = await self._check_new_materials(forceid, record)

                    if has_new_materials:
                        # 标记为已收到
                        await self._mark_supplementary_received(forceid, record)
                        received_count += 1

                except Exception as e:
                    LOGGER.error(f"检查补件接收失败: {forceid}, 错误: {e}")

            message = f"检查完成: 收到 {received_count} 个补件"
            LOGGER.info(message)
            return received_count, message

        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"检查补件接收失败: {error_msg}")
            return 0, f"检查失败: {error_msg}"

    async def _reject_claim(self, forceid: str, reason: str):
        """
        拒绝案件

        Args:
            forceid: 案件唯一ID
            reason: 拒绝原因
        """
        LOGGER.info(f"拒绝案件: {forceid}, 原因: {reason}")

        # 更新案件状态为拒绝
        await self.status_manager.update_claim_status(
            forceid,
            ClaimStatus.REJECTED,
            reason
        )

        # 更新补件记录状态
        records = await self.supplementary_dao.get_supplementary_records(forceid)
        for record in records:
            if record.status in [SupplementaryStatus.PENDING, SupplementaryStatus.REQUESTED]:
                await self.supplementary_dao.update_supplementary_status(
                    record.id,
                    SupplementaryStatus.REJECTED
                )

    async def _extract_missing_materials(self, review_result: Dict[str, Any]) -> List[str]:
        """
        从审核结果中提取缺失材料

        Args:
            review_result: 审核结果

        Returns:
            缺失材料列表
        """
        # 尝试从不同字段提取
        missing_materials = []

        # 1. 从KeyConclusions提取
        key_conclusions = review_result.get("KeyConclusions", [])
        if isinstance(key_conclusions, list):
            for conclusion in key_conclusions:
                if isinstance(conclusion, dict):
                    remark = conclusion.get("Remark", "")
                    if "缺" in remark or "缺少" in remark or "缺失" in remark:
                        missing_materials.append(remark)

        # 2. 从Remark提取
        remark = review_result.get("Remark", "")
        if "缺" in remark or "缺少" in remark or "缺失" in remark:
            missing_materials.append(remark)

        # 3. 从DebugInfo提取
        debug_info = review_result.get("DebugInfo", {})
        missing_from_debug = debug_info.get("missing_materials", [])
        if isinstance(missing_from_debug, list):
            missing_materials.extend(missing_from_debug)

        # 去重
        return list(set(missing_materials)) if missing_materials else ["请按要求补齐材料"]

    async def _notify_frontend_supplementary(
        self,
        forceid: str,
        record: SupplementaryRecord
    ):
        """
        通知前端需要补件

        Args:
            forceid: 案件唯一ID
            record: 补件记录
        """
        LOGGER.info(f"通知前端补件: {forceid}")

        # TODO: 调用前端API发送补件通知
        # payload = {
        #     "forceid": forceid,
        #     "supplementary_number": record.supplementary_number,
        #     "reason": record.requested_reason,
        #     "materials": record.required_materials,
        #     "deadline": record.deadline.isoformat()
        # }
        # await self._call_frontend_api(payload)

    async def _send_reminder(self, forceid: str, record: SupplementaryRecord):
        """
        发送补件提醒

        Args:
            forceid: 案件唯一ID
            record: 补件记录
        """
        LOGGER.info(f"发送补件提醒: {forceid}")

        # TODO: 调用前端API发送提醒
        # payload = {
        #     "forceid": forceid,
        #     "supplementary_number": record.supplementary_number,
        #     "deadline": record.deadline.isoformat(),
        #     "message": "您的案件需要补件，请尽快处理"
        # }
        # await self._call_frontend_api(payload)

    async def _handle_supplementary_timeout(self, forceid: str, record: SupplementaryRecord):
        """
        处理补件超时

        Args:
            forceid: 案件唯一ID
            record: 补件记录
        """
        LOGGER.info(f"处理补件超时: {forceid}")

        # 更新补件记录状态
        await self.supplementary_dao.update_supplementary_status(
            record.id,
            SupplementaryStatus.TIMEOUT
        )

        # 检查是否超过最大补件次数
        status_record = await self.status_manager.claim_status_dao.get_status_by_forceid(forceid)
        if status_record and status_record.supplementary_count >= self.max_supplementary_count:
            # 超过次数，拒绝
            await self._reject_claim(forceid, "补件超时且已达到最大补件次数")
        else:
            # 未超过次数，继续等待或创建新的补件请求
            LOGGER.info(f"补件超时但未超过最大次数: {forceid}")

    async def _check_new_materials(self, forceid: str, record: SupplementaryRecord) -> bool:
        """
        检查是否有新上传的补件材料

        Args:
            forceid: 案件唯一ID
            record: 补件记录

        Returns:
            是否有新材料
        """
        # TODO: 实现材料检查逻辑
        # 可以通过以下方式检查:
        # 1. 调用上游API查询案件材料更新时间
        # 2. 检查本地文件更新时间
        # 3. 比较材料数量或内容

        # 示例实现：始终返回False（需要根据实际情况修改）
        return False

    async def _mark_supplementary_received(self, forceid: str, record: SupplementaryRecord):
        """
        标记补件已收到

        Args:
            forceid: 案件唯一ID
            record: 补件记录
        """
        LOGGER.info(f"标记补件已收到: {forceid}")

        # 更新补件记录状态
        await self.supplementary_dao.update_supplementary_status(
            record.id,
            SupplementaryStatus.RECEIVED
        )

        # 更新案件状态
        await self.status_manager.update_claim_status(
            forceid,
            ClaimStatus.SUPPLEMENTARY_RECEIVED,
            "收到补件材料"
        )


# 全局实例
_supplementary_handler = None


def get_supplementary_handler() -> SupplementaryHandler:
    """获取补件处理器实例"""
    global _supplementary_handler
    if _supplementary_handler is None:
        _supplementary_handler = SupplementaryHandler()
    return _supplementary_handler


async def run_supplementary_check():
    """运行补件检查（用于定时任务）"""
    handler = get_supplementary_handler()
    await handler.initialize()

    try:
        # 1. 检查截止时间并发送提醒
        reminder_count, reminder_msg = await handler.check_supplementary_deadline()
        print(f"补件提醒: {reminder_msg}")

        # 2. 检查超时
        timeout_count, timeout_msg = await handler.check_supplementary_timeout()
        print(f"补件超时: {timeout_msg}")

        # 3. 检查收到补件
        received_count, received_msg = await handler.check_supplementary_received()
        print(f"补件接收: {received_msg}")

        return {
            "reminder": reminder_count,
            "timeout": timeout_count,
            "received": received_count
        }

    finally:
        await handler.db.close()


if __name__ == '__main__':
    # 测试
    import asyncio

    async def test():
        handler = SupplementaryHandler()
        await handler.initialize()

        try:
            result = await run_supplementary_check()
            print(f"结果: {result}")
        finally:
            await handler.db.close()

    asyncio.run(test())