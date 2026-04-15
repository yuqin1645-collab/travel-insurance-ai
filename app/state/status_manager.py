#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
状态管理器
负责案件状态的创建、更新和查询
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from contextlib import asynccontextmanager

from app.db.models import (
    ClaimStatusRecord, ReviewResult, SupplementaryRecord,
    ClaimStatus, DownloadStatus, ReviewStatus, SupplementaryStatus
)
from app.db.database import (
    get_claim_status_dao, get_review_result_dao, get_supplementary_dao,
    ClaimStatusDAO, ReviewResultDAO, SupplementaryDAO
)
from app.state.claim_state_machine import ClaimStateMachine, StateTransitionError

LOGGER = logging.getLogger(__name__)


class StatusManager:
    """状态管理器"""

    def __init__(
        self,
        claim_status_dao: Optional[ClaimStatusDAO] = None,
        review_result_dao: Optional[ReviewResultDAO] = None,
        supplementary_dao: Optional[SupplementaryDAO] = None
    ):
        self.claim_status_dao = claim_status_dao or get_claim_status_dao()
        self.review_result_dao = review_result_dao or get_review_result_dao()
        self.supplementary_dao = supplementary_dao or get_supplementary_dao()

    async def create_claim_status(
        self,
        claim_id: str,
        forceid: str,
        claim_type: str,
        initial_status: str = ClaimStatus.DOWNLOAD_PENDING
    ) -> ClaimStatusRecord:
        """
        创建案件状态记录

        Args:
            claim_id: 上游案件ID
            forceid: 案件唯一ID
            claim_type: 案件类型
            initial_status: 初始状态

        Returns:
            创建的状态记录
        """
        LOGGER.info(f"创建案件状态记录: {forceid} ({claim_type})")

        # 检查是否已存在
        existing = await self.claim_status_dao.get_status_by_forceid(forceid)
        if existing:
            LOGGER.warning(f"案件状态记录已存在: {forceid}")
            return existing

        # 创建新记录
        status_record = ClaimStatusRecord(
            claim_id=claim_id,
            forceid=forceid,
            claim_type=claim_type,
            current_status=initial_status,
            status_changed_at=datetime.now(),
            download_status=DownloadStatus.PENDING,
            review_status=ReviewStatus.PENDING
        )

        # 保存到数据库
        await self.claim_status_dao.create_or_update_status(status_record)

        LOGGER.info(f"✓ 案件状态记录创建成功: {forceid}")
        return status_record

    async def update_claim_status(
        self,
        forceid: str,
        new_status: str,
        change_reason: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        更新案件状态

        Args:
            forceid: 案件唯一ID
            new_status: 新状态
            change_reason: 变更原因
            error_message: 错误信息

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"更新案件状态: {forceid} -> {new_status}")

        # 获取当前状态
        status_record = await self.claim_status_dao.get_status_by_forceid(forceid)
        if not status_record:
            return False, f"案件状态记录不存在: {forceid}"

        # 检查状态转换是否允许
        can_transition, reason = ClaimStateMachine.can_transition(
            status_record.current_status, new_status, status_record
        )

        if not can_transition:
            error_msg = f"状态转换不允许: {status_record.current_status} -> {new_status}, 原因: {reason}"
            LOGGER.error(error_msg)
            return False, error_msg

        # 更新状态记录
        status_record.previous_status = status_record.current_status
        status_record.current_status = new_status
        status_record.status_changed_at = datetime.now()

        # 根据状态类型更新子状态
        if new_status in [
            ClaimStatus.DOWNLOAD_PENDING, ClaimStatus.DOWNLOADING,
            ClaimStatus.DOWNLOADED, ClaimStatus.DOWNLOAD_FAILED
        ]:
            status_record.download_status = new_status.split('_')[-1].upper()
            if new_status == ClaimStatus.DOWNLOAD_FAILED:
                status_record.download_attempts += 1
            elif new_status == ClaimStatus.DOWNLOADED:
                status_record.last_download_time = datetime.now()

        elif new_status in [
            ClaimStatus.REVIEW_PENDING, ClaimStatus.REVIEWING,
            ClaimStatus.REVIEWED, ClaimStatus.SUPPLEMENTARY_NEEDED
        ]:
            status_record.review_status = new_status.split('_')[-1].upper()
            if new_status == ClaimStatus.REVIEWED:
                status_record.last_review_time = datetime.now()
            elif new_status == ClaimStatus.SUPPLEMENTARY_NEEDED:
                status_record.supplementary_count += 1

        # 设置错误信息
        if error_message:
            status_record.error_message = error_message

        # 计算下次检查时间
        attempt_count = max(
            status_record.download_attempts,
            status_record.review_attempts,
            status_record.supplementary_count
        )
        status_record.next_check_time = ClaimStateMachine.get_next_check_time(
            new_status, attempt_count
        )

        # 保存到数据库
        await self.claim_status_dao.create_or_update_status(status_record)

        # 更新当前状态
        await self.claim_status_dao.update_current_status(
            forceid, new_status, change_reason
        )

        LOGGER.info(f"✓ 案件状态更新成功: {forceid} -> {new_status}")
        return True, "状态更新成功"

    async def update_download_status(
        self,
        forceid: str,
        download_status: str,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        更新下载状态

        Args:
            forceid: 案件唯一ID
            download_status: 下载状态
            success: 是否成功
            error_message: 错误信息

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"更新下载状态: {forceid} -> {download_status}")

        # 获取当前状态
        status_record = await self.claim_status_dao.get_status_by_forceid(forceid)
        if not status_record:
            return False, f"案件状态记录不存在: {forceid}"

        # 更新下载状态
        updated = await self.claim_status_dao.update_download_status(
            forceid,
            download_status,
            error_message,
            ClaimStateMachine.get_next_check_time(download_status, status_record.download_attempts)
        )

        if not updated:
            return False, f"更新下载状态失败: {forceid}"

        # 如果下载完成，更新整体状态
        if download_status == DownloadStatus.COMPLETED:
            await self.update_claim_status(
                forceid,
                ClaimStatus.DOWNLOADED,
                "下载完成"
            )
        elif download_status == DownloadStatus.FAILED:
            await self.update_claim_status(
                forceid,
                ClaimStatus.DOWNLOAD_FAILED,
                "下载失败",
                error_message
            )

        LOGGER.info(f"✓ 下载状态更新成功: {forceid} -> {download_status}")
        return True, "下载状态更新成功"

    async def update_review_status(
        self,
        forceid: str,
        review_result: Dict[str, Any],
        success: bool = True,
        error_message: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        更新审核状态

        Args:
            forceid: 案件唯一ID
            review_result: 审核结果
            success: 是否成功
            error_message: 错误信息

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"更新审核状态: {forceid}")

        # 获取当前状态
        status_record = await self.claim_status_dao.get_status_by_forceid(forceid)
        if not status_record:
            return False, f"案件状态记录不存在: {forceid}"

        # 确定审核状态
        if not success:
            review_status = ReviewStatus.FAILED
        elif review_result.get("IsAdditional") == "Y":
            review_status = ReviewStatus.SUPPLEMENTARY_NEEDED
        else:
            review_status = ReviewStatus.COMPLETED

        # 更新审核状态
        supplementary_count = None
        if review_status == ReviewStatus.SUPPLEMENTARY_NEEDED:
            supplementary_count = status_record.supplementary_count + 1

        updated = await self.claim_status_dao.update_review_status(
            forceid,
            review_status,
            supplementary_count,
            error_message,
            ClaimStateMachine.get_next_check_time(review_status, status_record.review_attempts)
        )

        if not updated:
            return False, f"更新审核状态失败: {forceid}"

        # 保存审核结果（失败时不写空记录）
        if success and review_result:
            await self._save_review_result(forceid, review_result, review_status)

        # 更新整体状态
        if review_status == ReviewStatus.COMPLETED:
            final_decision = review_result.get("final_decision", "rejected")
            if final_decision == "approved":
                await self.update_claim_status(
                    forceid,
                    ClaimStatus.APPROVED,
                    "审核通过"
                )
            else:
                await self.update_claim_status(
                    forceid,
                    ClaimStatus.REJECTED,
                    "审核拒绝"
                )
        elif review_status == ReviewStatus.SUPPLEMENTARY_NEEDED:
            await self.update_claim_status(
                forceid,
                ClaimStatus.SUPPLEMENTARY_NEEDED,
                "需补件"
            )
            # 创建补件记录
            await self._create_supplementary_record(forceid, review_result)
        elif review_status == ReviewStatus.FAILED:
            await self.update_claim_status(
                forceid,
                ClaimStatus.ERROR,
                "审核失败",
                error_message
            )

        LOGGER.info(f"✓ 审核状态更新成功: {forceid} -> {review_status}")
        return True, "审核状态更新成功"

    async def update_supplementary_status(
        self,
        forceid: str,
        supplementary_id: int,
        status: str,
        completed_materials: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """
        更新补件状态

        Args:
            forceid: 案件唯一ID
            supplementary_id: 补件记录ID
            status: 补件状态
            completed_materials: 已补材料列表

        Returns:
            (是否成功, 消息)
        """
        LOGGER.info(f"更新补件状态: {forceid} -> {status}")

        # 更新补件状态
        updated = await self.supplementary_dao.update_supplementary_status(
            supplementary_id, status, completed_materials
        )

        if not updated:
            return False, f"更新补件状态失败: {forceid}"

        # 如果收到补件，更新案件状态
        if status == SupplementaryStatus.RECEIVED:
            await self.update_claim_status(
                forceid,
                ClaimStatus.SUPPLEMENTARY_RECEIVED,
                "收到补件"
            )

        LOGGER.info(f"✓ 补件状态更新成功: {forceid} -> {status}")
        return True, "补件状态更新成功"

    async def get_claim_status(self, forceid: str) -> Optional[Dict[str, Any]]:
        """
        获取案件状态详情

        Args:
            forceid: 案件唯一ID

        Returns:
            状态详情字典
        """
        # 获取状态记录
        status_record = await self.claim_status_dao.get_status_by_forceid(forceid)
        if not status_record:
            return None

        # 获取审核结果
        review_result = await self.review_result_dao.get_result_by_forceid(forceid)

        # 获取补件记录
        supplementary_records = await self.supplementary_dao.get_supplementary_records(forceid)

        # 构建状态详情
        status_detail = {
            "forceid": forceid,
            "claim_id": status_record.claim_id,
            "claim_type": status_record.claim_type,
            "current_status": status_record.current_status,
            "current_status_description": ClaimStateMachine.get_status_description(
                status_record.current_status
            ),
            "previous_status": status_record.previous_status,
            "status_changed_at": status_record.status_changed_at.isoformat() if status_record.status_changed_at else None,
            "download_status": status_record.download_status,
            "download_attempts": status_record.download_attempts,
            "last_download_time": status_record.last_download_time.isoformat() if status_record.last_download_time else None,
            "review_status": status_record.review_status,
            "review_attempts": status_record.review_attempts,
            "last_review_time": status_record.last_review_time.isoformat() if status_record.last_review_time else None,
            "supplementary_count": status_record.supplementary_count,
            "max_supplementary": status_record.max_supplementary,
            "next_check_time": status_record.next_check_time.isoformat() if status_record.next_check_time else None,
            "error_message": status_record.error_message,
            "created_at": status_record.created_at.isoformat() if status_record.created_at else None,
            "updated_at": status_record.updated_at.isoformat() if status_record.updated_at else None,
            "review_result": review_result.to_dict() if review_result else None,
            "supplementary_records": [record.to_dict() for record in supplementary_records],
            "status_category": ClaimStateMachine.get_status_category(status_record.current_status),
            "is_final_status": ClaimStateMachine.is_final_status(status_record.current_status),
            "is_error_status": ClaimStateMachine.is_error_status(status_record.current_status),
            "requires_human_intervention": ClaimStateMachine.requires_human_intervention(status_record.current_status),
            "recommended_action": ClaimStateMachine.get_recommended_action(status_record.current_status),
        }

        return status_detail

    async def get_pending_claims(
        self,
        status_filter: Optional[List[str]] = None,
        claim_type: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        获取待处理案件

        Args:
            status_filter: 状态过滤器
            claim_type: 案件类型过滤器
            limit: 限制数量

        Returns:
            待处理案件列表
        """
        LOGGER.info(f"获取待处理案件: status_filter={status_filter}, claim_type={claim_type}, limit={limit}")

        # 获取待下载案件
        pending_downloads = await self.claim_status_dao.get_pending_downloads(limit)

        # 获取待审核案件
        pending_reviews = await self.claim_status_dao.get_pending_reviews(limit)

        # 合并并去重
        all_pending = {}
        for record in pending_downloads + pending_reviews:
            if record.forceid not in all_pending:
                all_pending[record.forceid] = record

        # 应用过滤器
        filtered_records = []
        for record in all_pending.values():
            # 状态过滤器
            if status_filter and record.current_status not in status_filter:
                continue

            # 案件类型过滤器
            if claim_type and record.claim_type != claim_type:
                continue

            filtered_records.append(record)

        # 按下次检查时间排序
        filtered_records.sort(key=lambda r: r.next_check_time or datetime.max)

        # 限制数量
        filtered_records = filtered_records[:limit]

        # 获取详情
        pending_details = []
        for record in filtered_records:
            detail = await self.get_claim_status(record.forceid)
            if detail:
                pending_details.append(detail)

        LOGGER.info(f"找到 {len(pending_details)} 个待处理案件")
        return pending_details

    async def get_claim_statistics(self) -> Dict[str, Any]:
        """
        获取案件统计信息

        Returns:
            统计信息字典
        """
        # TODO: 实现统计查询
        # 这里需要添加具体的统计查询逻辑
        return {
            "total_claims": 0,
            "by_status": {},
            "by_type": {},
            "today_processed": 0,
            "success_rate": 0.0,
        }

    async def cleanup_expired_claims(self, days_to_keep: int = 30) -> Tuple[int, str]:
        """
        清理过期案件

        Args:
            days_to_keep: 保留天数

        Returns:
            (清理数量, 消息)
        """
        # TODO: 实现清理逻辑
        # 这里需要添加清理过期案件的逻辑
        return 0, "清理功能待实现"

    async def _save_review_result(
        self,
        forceid: str,
        review_result: Dict[str, Any],
        review_status: str
    ):
        """保存审核结果"""
        # 构建审核结果记录
        result_record = ReviewResult(
            forceid=forceid,
            claim_id=review_result.get("claim_id"),
            remark=review_result.get("Remark", ""),
            is_additional=review_result.get("IsAdditional", "Y"),
            key_conclusions=json.dumps(review_result.get("KeyConclusions", []), ensure_ascii=False) if review_result.get("KeyConclusions") is not None else None,
            raw_result=str(review_result),
            audit_status=review_status,
            supplementary_count=review_result.get("supplementary_count", 0),
            supplementary_reason=review_result.get("supplementary_reason"),
            final_decision=review_result.get("final_decision"),
            decision_reason=review_result.get("decision_reason"),
            metadata={
                "review_timestamp": datetime.now().isoformat(),
                "source": "ai_review"
            }
        )

        # 保存到数据库
        await self.review_result_dao.create_or_update_result(result_record)

    async def _create_supplementary_record(
        self,
        forceid: str,
        review_result: Dict[str, Any]
    ):
        """创建补件记录"""
        from datetime import timedelta
        from app.config import config

        # 获取案件状态
        status_record = await self.claim_status_dao.get_status_by_forceid(forceid)
        if not status_record:
            return

        # 构建补件记录
        supplementary_record = SupplementaryRecord(
            claim_id=status_record.claim_id,
            forceid=forceid,
            supplementary_number=status_record.supplementary_count,
            requested_at=datetime.now(),
            requested_reason=review_result.get("Remark", "需补件"),
            required_materials=review_result.get("missing_materials", []),
            deadline=datetime.now() + timedelta(hours=config.SUPPLEMENTARY_DEADLINE_HOURS),
            status=SupplementaryStatus.REQUESTED
        )

        # 保存到数据库
        await self.supplementary_dao.create_supplementary_record(supplementary_record)


# 全局状态管理器实例
_status_manager = None


def get_status_manager() -> StatusManager:
    """获取状态管理器实例"""
    global _status_manager
    if _status_manager is None:
        _status_manager = StatusManager()
    return _status_manager


@asynccontextmanager
async def status_transaction(forceid: str, new_status: str, change_reason: str = ""):
    """
    状态更新事务上下文管理器

    Args:
        forceid: 案件唯一ID
        new_status: 新状态
        change_reason: 变更原因

    Yields:
        状态管理器
    """
    manager = get_status_manager()
    try:
        yield manager
        # 事务成功，更新状态
        await manager.update_claim_status(forceid, new_status, change_reason)
    except Exception as e:
        # 事务失败，记录错误
        LOGGER.error(f"状态更新事务失败: {forceid}, 错误: {e}")
        await manager.update_claim_status(
            forceid,
            ClaimStatus.ERROR,
            f"事务失败: {change_reason}",
            str(e)
        )
        raise