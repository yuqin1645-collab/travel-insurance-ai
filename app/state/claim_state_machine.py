#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
案件状态机
定义案件生命周期状态流转规则
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from enum import Enum

from app.db.models import (
    ClaimStatus, DownloadStatus, ReviewStatus, SupplementaryStatus,
    ClaimStatusRecord
)
from app.config import config

LOGGER = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """状态转换错误"""
    pass


class ClaimStateMachine:
    """案件状态机"""

    # 状态流转规则
    TRANSITION_RULES = {
        # 下载状态流转
        DownloadStatus.PENDING: [DownloadStatus.DOWNLOADING, DownloadStatus.FAILED],
        DownloadStatus.DOWNLOADING: [DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.RETRYING],
        DownloadStatus.RETRYING: [DownloadStatus.DOWNLOADING, DownloadStatus.FAILED],
        DownloadStatus.FAILED: [DownloadStatus.RETRYING, DownloadStatus.PENDING],
        DownloadStatus.COMPLETED: [],

        # 审核状态流转
        ReviewStatus.PENDING: [ReviewStatus.PROCESSING, ReviewStatus.FAILED],
        ReviewStatus.PROCESSING: [ReviewStatus.COMPLETED, ReviewStatus.FAILED, ReviewStatus.SUPPLEMENTARY_NEEDED],
        ReviewStatus.SUPPLEMENTARY_NEEDED: [ReviewStatus.PENDING, ReviewStatus.FAILED],
        ReviewStatus.FAILED: [ReviewStatus.PENDING, ReviewStatus.PROCESSING],
        ReviewStatus.COMPLETED: [],

        # 补件状态流转
        SupplementaryStatus.PENDING: [SupplementaryStatus.REQUESTED, SupplementaryStatus.TIMEOUT],
        SupplementaryStatus.REQUESTED: [SupplementaryStatus.RECEIVED, SupplementaryStatus.TIMEOUT, SupplementaryStatus.REJECTED],
        SupplementaryStatus.RECEIVED: [SupplementaryStatus.VERIFIED, SupplementaryStatus.REJECTED],
        SupplementaryStatus.VERIFIED: [],
        SupplementaryStatus.TIMEOUT: [],
        SupplementaryStatus.REJECTED: [],

        # 整体状态流转
        ClaimStatus.DOWNLOAD_PENDING: [ClaimStatus.DOWNLOADING, ClaimStatus.DOWNLOAD_FAILED],
        ClaimStatus.DOWNLOADING: [ClaimStatus.DOWNLOADED, ClaimStatus.DOWNLOAD_FAILED],
        ClaimStatus.DOWNLOADED: [ClaimStatus.REVIEW_PENDING, ClaimStatus.ERROR],
        ClaimStatus.DOWNLOAD_FAILED: [ClaimStatus.DOWNLOAD_PENDING, ClaimStatus.ERROR],

        ClaimStatus.REVIEW_PENDING: [ClaimStatus.REVIEWING, ClaimStatus.ERROR],
        ClaimStatus.REVIEWING: [ClaimStatus.REVIEWED, ClaimStatus.SUPPLEMENTARY_NEEDED, ClaimStatus.ERROR],
        ClaimStatus.REVIEWED: [ClaimStatus.APPROVED, ClaimStatus.REJECTED, ClaimStatus.SUPPLEMENTARY_NEEDED],

        ClaimStatus.SUPPLEMENTARY_NEEDED: [ClaimStatus.PENDING_SUPPLEMENTARY, ClaimStatus.REJECTED],
        ClaimStatus.PENDING_SUPPLEMENTARY: [ClaimStatus.SUPPLEMENTARY_RECEIVED, ClaimStatus.REJECTED],
        ClaimStatus.SUPPLEMENTARY_RECEIVED: [ClaimStatus.REVIEW_PENDING, ClaimStatus.REJECTED],

        ClaimStatus.APPROVED: [ClaimStatus.COMPLETED],
        ClaimStatus.REJECTED: [ClaimStatus.COMPLETED],
        ClaimStatus.COMPLETED: [],

        ClaimStatus.ERROR: [ClaimStatus.DOWNLOAD_PENDING, ClaimStatus.REVIEW_PENDING],
        ClaimStatus.MAX_RETRIES_EXCEEDED: [ClaimStatus.REJECTED],
    }

    # 状态转换条件
    TRANSITION_CONDITIONS = {
        # 下载失败重试条件
        (DownloadStatus.FAILED, DownloadStatus.RETRYING): lambda r: r.download_attempts < config.MAX_DOWNLOAD_RETRIES,

        # 审核失败重试条件
        (ReviewStatus.FAILED, ReviewStatus.PENDING): lambda r: r.review_attempts < config.MAX_REVIEW_RETRIES,

        # 补件次数限制
        (ClaimStatus.SUPPLEMENTARY_NEEDED, ClaimStatus.PENDING_SUPPLEMENTARY): lambda r: r.supplementary_count < config.MAX_SUPPLEMENTARY_COUNT,
        (ClaimStatus.SUPPLEMENTARY_NEEDED, ClaimStatus.REJECTED): lambda r: r.supplementary_count >= config.MAX_SUPPLEMENTARY_COUNT,

        # 最大重试次数检查
        (ClaimStatus.ERROR, ClaimStatus.DOWNLOAD_PENDING): lambda r: r.download_attempts < config.MAX_DOWNLOAD_RETRIES,
        (ClaimStatus.ERROR, ClaimStatus.REVIEW_PENDING): lambda r: r.review_attempts < config.MAX_REVIEW_RETRIES,
        (ClaimStatus.ERROR, ClaimStatus.MAX_RETRIES_EXCEEDED): lambda r: (
            r.download_attempts >= config.MAX_DOWNLOAD_RETRIES or
            r.review_attempts >= config.MAX_REVIEW_RETRIES
        ),
    }

    @classmethod
    def can_transition(
        cls,
        from_status: str,
        to_status: str,
        record: Optional[ClaimStatusRecord] = None
    ) -> Tuple[bool, str]:
        """
        检查状态转换是否允许

        Args:
            from_status: 当前状态
            to_status: 目标状态
            record: 案件状态记录（可选）

        Returns:
            (是否允许, 原因)
        """
        # 相同状态允许转换（用于更新）
        if from_status == to_status:
            return True, "相同状态更新"

        # 检查是否在允许的转换列表中
        allowed_transitions = cls.TRANSITION_RULES.get(from_status, [])
        if to_status not in allowed_transitions:
            return False, f"不允许从 {from_status} 转换到 {to_status}"

        # 检查转换条件
        condition_key = (from_status, to_status)
        if condition_key in cls.TRANSITION_CONDITIONS:
            if not record:
                return False, f"需要案件记录来验证转换条件"

            condition_func = cls.TRANSITION_CONDITIONS[condition_key]
            if not condition_func(record):
                return False, f"不满足转换条件"

        return True, "允许转换"

    @classmethod
    def get_next_check_time(
        cls,
        current_status: str,
        attempt_count: int = 0
    ) -> Optional[datetime]:
        """
        根据状态和尝试次数计算下次检查时间

        Args:
            current_status: 当前状态
            attempt_count: 尝试次数

        Returns:
            下次检查时间
        """
        now = datetime.now()

        # 根据状态和尝试次数计算延迟
        if current_status in [DownloadStatus.FAILED, ReviewStatus.FAILED, ClaimStatus.ERROR]:
            # 指数退避
            delay_seconds = config.RETRY_BACKOFF_BASE ** attempt_count * 60  # 分钟转秒
            return now + timedelta(seconds=min(delay_seconds, 3600))  # 最多1小时

        elif current_status == ClaimStatus.PENDING_SUPPLEMENTARY:
            # 补件提醒时间
            reminder_hours = config.SUPPLEMENTARY_REMINDER_HOURS
            return now + timedelta(hours=reminder_hours)

        elif current_status in [DownloadStatus.PENDING, ReviewStatus.PENDING]:
            # 待处理状态，立即检查
            return now

        # 其他状态不需要定时检查
        return None

    @classmethod
    def get_expected_next_status(
        cls,
        current_status: str,
        action_result: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        根据当前状态和操作结果预测下一个状态

        Args:
            current_status: 当前状态
            action_result: 操作结果（如AI审核结果）

        Returns:
            预期的下一个状态
        """
        # 下载相关状态
        if current_status == DownloadStatus.DOWNLOADING:
            return DownloadStatus.COMPLETED

        # 审核相关状态
        if current_status == ReviewStatus.PROCESSING:
            if action_result:
                if action_result.get("IsAdditional") == "Y":
                    return ReviewStatus.SUPPLEMENTARY_NEEDED
                else:
                    return ReviewStatus.COMPLETED
            return ReviewStatus.COMPLETED

        # 补件相关状态
        if current_status == SupplementaryStatus.REQUESTED:
            return SupplementaryStatus.RECEIVED

        # 整体状态
        if current_status == ClaimStatus.DOWNLOADING:
            return ClaimStatus.DOWNLOADED

        if current_status == ClaimStatus.REVIEWING:
            if action_result:
                if action_result.get("IsAdditional") == "Y":
                    return ClaimStatus.SUPPLEMENTARY_NEEDED
                else:
                    # 根据审核结果决定
                    if action_result.get("final_decision") == "approved":
                        return ClaimStatus.APPROVED
                    else:
                        return ClaimStatus.REJECTED
            return ClaimStatus.REVIEWED

        if current_status == ClaimStatus.SUPPLEMENTARY_NEEDED:
            return ClaimStatus.PENDING_SUPPLEMENTARY

        if current_status == ClaimStatus.PENDING_SUPPLEMENTARY:
            return ClaimStatus.SUPPLEMENTARY_RECEIVED

        if current_status == ClaimStatus.SUPPLEMENTARY_RECEIVED:
            return ClaimStatus.REVIEW_PENDING

        # 默认返回当前状态
        return current_status

    @classmethod
    def validate_status_consistency(
        cls,
        record: ClaimStatusRecord
    ) -> Tuple[bool, str]:
        """
        验证状态记录的一致性

        Args:
            record: 案件状态记录

        Returns:
            (是否一致, 不一致的原因)
        """
        # 检查下载状态和整体状态的一致性
        if record.download_status == DownloadStatus.COMPLETED:
            if record.current_status not in [
                ClaimStatus.DOWNLOADED,
                ClaimStatus.REVIEW_PENDING,
                ClaimStatus.REVIEWING,
                ClaimStatus.REVIEWED,
                ClaimStatus.SUPPLEMENTARY_NEEDED,
                ClaimStatus.PENDING_SUPPLEMENTARY,
                ClaimStatus.SUPPLEMENTARY_RECEIVED,
                ClaimStatus.APPROVED,
                ClaimStatus.REJECTED,
                ClaimStatus.COMPLETED
            ]:
                return False, f"下载完成但整体状态为 {record.current_status}"

        # 检查审核状态和整体状态的一致性
        if record.review_status == ReviewStatus.COMPLETED:
            if record.current_status not in [
                ClaimStatus.REVIEWED,
                ClaimStatus.APPROVED,
                ClaimStatus.REJECTED,
                ClaimStatus.COMPLETED
            ]:
                return False, f"审核完成但整体状态为 {record.current_status}"

        # 检查补件次数
        if record.supplementary_count > record.max_supplementary:
            return False, f"补件次数 {record.supplementary_count} 超过最大限制 {record.max_supplementary}"

        # 检查时间顺序
        if record.last_download_time and record.last_review_time:
            if record.last_download_time > record.last_review_time:
                return False, "下载时间晚于审核时间"

        if record.status_changed_at > datetime.now():
            return False, "状态变更时间在未来"

        return True, "状态一致"

    @classmethod
    def get_status_description(cls, status: str) -> str:
        """获取状态描述"""
        descriptions = {
            # 下载状态
            DownloadStatus.PENDING: "等待下载",
            DownloadStatus.DOWNLOADING: "下载中",
            DownloadStatus.COMPLETED: "下载完成",
            DownloadStatus.FAILED: "下载失败",
            DownloadStatus.RETRYING: "重试下载",

            # 审核状态
            ReviewStatus.PENDING: "等待审核",
            ReviewStatus.PROCESSING: "审核中",
            ReviewStatus.COMPLETED: "审核完成",
            ReviewStatus.FAILED: "审核失败",
            ReviewStatus.SUPPLEMENTARY_NEEDED: "需补件",

            # 补件状态
            SupplementaryStatus.PENDING: "等待补件",
            SupplementaryStatus.REQUESTED: "已请求补件",
            SupplementaryStatus.RECEIVED: "已收到补件",
            SupplementaryStatus.VERIFIED: "补件已验证",
            SupplementaryStatus.TIMEOUT: "补件超时",
            SupplementaryStatus.REJECTED: "补件被拒绝",

            # 整体状态
            ClaimStatus.DOWNLOAD_PENDING: "等待下载",
            ClaimStatus.DOWNLOADING: "下载中",
            ClaimStatus.DOWNLOADED: "下载完成",
            ClaimStatus.DOWNLOAD_FAILED: "下载失败",
            ClaimStatus.REVIEW_PENDING: "等待审核",
            ClaimStatus.REVIEWING: "审核中",
            ClaimStatus.REVIEWED: "审核完成",
            ClaimStatus.SUPPLEMENTARY_NEEDED: "需补件",
            ClaimStatus.PENDING_SUPPLEMENTARY: "等待补件",
            ClaimStatus.SUPPLEMENTARY_RECEIVED: "已收到补件",
            ClaimStatus.APPROVED: "审核通过",
            ClaimStatus.REJECTED: "审核拒绝",
            ClaimStatus.COMPLETED: "处理完成",
            ClaimStatus.ERROR: "系统错误",
            ClaimStatus.MAX_RETRIES_EXCEEDED: "超过最大重试次数",
        }

        return descriptions.get(status, status)

    @classmethod
    def get_status_category(cls, status: str) -> str:
        """获取状态类别"""
        if status in [
            DownloadStatus.PENDING, DownloadStatus.DOWNLOADING,
            DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.RETRYING
        ]:
            return "download"

        if status in [
            ReviewStatus.PENDING, ReviewStatus.PROCESSING,
            ReviewStatus.COMPLETED, ReviewStatus.FAILED, ReviewStatus.SUPPLEMENTARY_NEEDED
        ]:
            return "review"

        if status in [
            SupplementaryStatus.PENDING, SupplementaryStatus.REQUESTED,
            SupplementaryStatus.RECEIVED, SupplementaryStatus.VERIFIED,
            SupplementaryStatus.TIMEOUT, SupplementaryStatus.REJECTED
        ]:
            return "supplementary"

        if status in [
            ClaimStatus.DOWNLOAD_PENDING, ClaimStatus.DOWNLOADING,
            ClaimStatus.DOWNLOADED, ClaimStatus.DOWNLOAD_FAILED
        ]:
            return "download_overall"

        if status in [
            ClaimStatus.REVIEW_PENDING, ClaimStatus.REVIEWING,
            ClaimStatus.REVIEWED
        ]:
            return "review_overall"

        if status in [
            ClaimStatus.SUPPLEMENTARY_NEEDED, ClaimStatus.PENDING_SUPPLEMENTARY,
            ClaimStatus.SUPPLEMENTARY_RECEIVED
        ]:
            return "supplementary_overall"

        if status in [
            ClaimStatus.APPROVED, ClaimStatus.REJECTED, ClaimStatus.COMPLETED
        ]:
            return "final"

        if status in [ClaimStatus.ERROR, ClaimStatus.MAX_RETRIES_EXCEEDED]:
            return "error"

        return "unknown"

    @classmethod
    def is_final_status(cls, status: str) -> bool:
        """判断是否为最终状态"""
        final_statuses = [
            DownloadStatus.COMPLETED,
            ReviewStatus.COMPLETED,
            SupplementaryStatus.VERIFIED,
            SupplementaryStatus.TIMEOUT,
            SupplementaryStatus.REJECTED,
            ClaimStatus.APPROVED,
            ClaimStatus.REJECTED,
            ClaimStatus.COMPLETED,
            ClaimStatus.MAX_RETRIES_EXCEEDED,
        ]

        return status in final_statuses

    @classmethod
    def is_error_status(cls, status: str) -> bool:
        """判断是否为错误状态"""
        error_statuses = [
            DownloadStatus.FAILED,
            ReviewStatus.FAILED,
            SupplementaryStatus.TIMEOUT,
            SupplementaryStatus.REJECTED,
            ClaimStatus.DOWNLOAD_FAILED,
            ClaimStatus.ERROR,
            ClaimStatus.MAX_RETRIES_EXCEEDED,
        ]

        return status in error_statuses

    @classmethod
    def requires_human_intervention(cls, status: str) -> bool:
        """判断是否需要人工干预"""
        human_intervention_statuses = [
            ClaimStatus.MAX_RETRIES_EXCEEDED,
            ClaimStatus.ERROR,
        ]

        return status in human_intervention_statuses

    @classmethod
    def get_recommended_action(cls, status: str) -> str:
        """获取推荐操作"""
        action_mapping = {
            DownloadStatus.PENDING: "开始下载",
            DownloadStatus.DOWNLOADING: "继续下载",
            DownloadStatus.FAILED: "重试下载",
            DownloadStatus.RETRYING: "重试下载",

            ReviewStatus.PENDING: "开始审核",
            ReviewStatus.PROCESSING: "继续审核",
            ReviewStatus.FAILED: "重试审核",
            ReviewStatus.SUPPLEMENTARY_NEEDED: "请求补件",

            SupplementaryStatus.PENDING: "发送补件请求",
            SupplementaryStatus.REQUESTED: "等待补件",
            SupplementaryStatus.RECEIVED: "验证补件材料",

            ClaimStatus.DOWNLOAD_PENDING: "开始下载",
            ClaimStatus.DOWNLOADING: "继续下载",
            ClaimStatus.DOWNLOAD_FAILED: "重试下载",
            ClaimStatus.REVIEW_PENDING: "开始审核",
            ClaimStatus.REVIEWING: "继续审核",
            ClaimStatus.SUPPLEMENTARY_NEEDED: "请求补件",
            ClaimStatus.PENDING_SUPPLEMENTARY: "等待补件",
            ClaimStatus.SUPPLEMENTARY_RECEIVED: "重新审核",

            ClaimStatus.ERROR: "检查系统错误",
            ClaimStatus.MAX_RETRIES_EXCEEDED: "人工处理",
        }

        return action_mapping.get(status, "无操作")