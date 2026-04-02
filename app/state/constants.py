#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
状态常量定义
案件生命周期中使用的所有状态常量
"""

from enum import Enum


class ClaimStatus(str, Enum):
    """案件整体状态枚举"""
    # 下载相关
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    DOWNLOAD_FAILED = "download_failed"

    # 审核相关
    REVIEW_PENDING = "review_pending"
    REVIEWING = "reviewing"
    REVIEWED = "reviewed"

    # 补件相关
    SUPPLEMENTARY_NEEDED = "supplementary_needed"
    PENDING_SUPPLEMENTARY = "pending_supplementary"
    SUPPLEMENTARY_RECEIVED = "supplementary_received"

    # 最终状态
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"

    # 错误状态
    ERROR = "error"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"


class DownloadStatus(str, Enum):
    """下载状态枚举"""
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class ReviewStatus(str, Enum):
    """审核状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SUPPLEMENTARY_NEEDED = "supplementary_needed"


class SupplementaryStatus(str, Enum):
    """补件状态枚举"""
    PENDING = "pending"
    REQUESTED = "requested"
    RECEIVED = "received"
    VERIFIED = "verified"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class TaskType(str, Enum):
    """任务类型枚举"""
    DOWNLOAD = "download"
    REVIEW = "review"
    SYNC = "sync"
    SUPPLEMENTARY = "supplementary"
    CLEANUP = "cleanup"


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DecisionType(str, Enum):
    """审核决定类型枚举"""
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPPLEMENTARY_NEEDED = "supplementary_needed"
    PENDING = "pending"


class ErrorType(str, Enum):
    """错误类型枚举"""
    NETWORK = "network"
    API = "api"
    DATABASE = "database"
    BUSINESS = "business"
    SYSTEM = "system"
    UNKNOWN = "unknown"


# 状态描述映射
STATUS_DESCRIPTIONS = {
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

    # 任务状态
    TaskStatus.PENDING: "等待执行",
    TaskStatus.RUNNING: "执行中",
    TaskStatus.SUCCESS: "执行成功",
    TaskStatus.FAILED: "执行失败",
    TaskStatus.CANCELLED: "已取消",

    # 决定类型
    DecisionType.APPROVED: "通过",
    DecisionType.REJECTED: "拒绝",
    DecisionType.SUPPLEMENTARY_NEEDED: "需补件",
    DecisionType.PENDING: "待决定",
}

# 状态类别映射
STATUS_CATEGORIES = {
    # 下载状态
    DownloadStatus.PENDING: "download",
    DownloadStatus.DOWNLOADING: "download",
    DownloadStatus.COMPLETED: "download",
    DownloadStatus.FAILED: "download",
    DownloadStatus.RETRYING: "download",

    # 审核状态
    ReviewStatus.PENDING: "review",
    ReviewStatus.PROCESSING: "review",
    ReviewStatus.COMPLETED: "review",
    ReviewStatus.FAILED: "review",
    ReviewStatus.SUPPLEMENTARY_NEEDED: "review",

    # 补件状态
    SupplementaryStatus.PENDING: "supplementary",
    SupplementaryStatus.REQUESTED: "supplementary",
    SupplementaryStatus.RECEIVED: "supplementary",
    SupplementaryStatus.VERIFIED: "supplementary",
    SupplementaryStatus.TIMEOUT: "supplementary",
    SupplementaryStatus.REJECTED: "supplementary",

    # 整体状态
    ClaimStatus.DOWNLOAD_PENDING: "download_overall",
    ClaimStatus.DOWNLOADING: "download_overall",
    ClaimStatus.DOWNLOADED: "download_overall",
    ClaimStatus.DOWNLOAD_FAILED: "download_overall",
    ClaimStatus.REVIEW_PENDING: "review_overall",
    ClaimStatus.REVIEWING: "review_overall",
    ClaimStatus.REVIEWED: "review_overall",
    ClaimStatus.SUPPLEMENTARY_NEEDED: "supplementary_overall",
    ClaimStatus.PENDING_SUPPLEMENTARY: "supplementary_overall",
    ClaimStatus.SUPPLEMENTARY_RECEIVED: "supplementary_overall",
    ClaimStatus.APPROVED: "final",
    ClaimStatus.REJECTED: "final",
    ClaimStatus.COMPLETED: "final",
    ClaimStatus.ERROR: "error",
    ClaimStatus.MAX_RETRIES_EXCEEDED: "error",

    # 任务状态
    TaskStatus.PENDING: "task",
    TaskStatus.RUNNING: "task",
    TaskStatus.SUCCESS: "task",
    TaskStatus.FAILED: "task",
    TaskStatus.CANCELLED: "task",
}

# 最终状态列表
FINAL_STATUSES = [
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

# 错误状态列表
ERROR_STATUSES = [
    DownloadStatus.FAILED,
    ReviewStatus.FAILED,
    SupplementaryStatus.TIMEOUT,
    SupplementaryStatus.REJECTED,
    ClaimStatus.DOWNLOAD_FAILED,
    ClaimStatus.ERROR,
    ClaimStatus.MAX_RETRIES_EXCEEDED,
]

# 需要人工干预的状态列表
HUMAN_INTERVENTION_STATUSES = [
    ClaimStatus.MAX_RETRIES_EXCEEDED,
    ClaimStatus.ERROR,
]

# 状态优先级（数字越小优先级越高）
STATUS_PRIORITY = {
    ClaimStatus.ERROR: 1,
    ClaimStatus.MAX_RETRIES_EXCEEDED: 2,
    ClaimStatus.DOWNLOAD_FAILED: 3,
    ClaimStatus.SUPPLEMENTARY_NEEDED: 4,
    ClaimStatus.PENDING_SUPPLEMENTARY: 5,
    ClaimStatus.DOWNLOAD_PENDING: 6,
    ClaimStatus.REVIEW_PENDING: 7,
    ClaimStatus.DOWNLOADING: 8,
    ClaimStatus.REVIEWING: 9,
    ClaimStatus.DOWNLOADED: 10,
    ClaimStatus.REVIEWED: 11,
    ClaimStatus.SUPPLEMENTARY_RECEIVED: 12,
    ClaimStatus.APPROVED: 13,
    ClaimStatus.REJECTED: 14,
    ClaimStatus.COMPLETED: 15,
}

# 状态颜色映射（用于UI显示）
STATUS_COLORS = {
    # 下载状态
    DownloadStatus.PENDING: "#ff9800",  # 橙色
    DownloadStatus.DOWNLOADING: "#2196f3",  # 蓝色
    DownloadStatus.COMPLETED: "#4caf50",  # 绿色
    DownloadStatus.FAILED: "#f44336",  # 红色
    DownloadStatus.RETRYING: "#ff9800",  # 橙色

    # 审核状态
    ReviewStatus.PENDING: "#ff9800",  # 橙色
    ReviewStatus.PROCESSING: "#2196f3",  # 蓝色
    ReviewStatus.COMPLETED: "#4caf50",  # 绿色
    ReviewStatus.FAILED: "#f44336",  # 红色
    ReviewStatus.SUPPLEMENTARY_NEEDED: "#ff9800",  # 橙色

    # 补件状态
    SupplementaryStatus.PENDING: "#ff9800",  # 橙色
    SupplementaryStatus.REQUESTED: "#2196f3",  # 蓝色
    SupplementaryStatus.RECEIVED: "#4caf50",  # 绿色
    SupplementaryStatus.VERIFIED: "#4caf50",  # 绿色
    SupplementaryStatus.TIMEOUT: "#f44336",  # 红色
    SupplementaryStatus.REJECTED: "#f44336",  # 红色

    # 整体状态
    ClaimStatus.DOWNLOAD_PENDING: "#ff9800",
    ClaimStatus.DOWNLOADING: "#2196f3",
    ClaimStatus.DOWNLOADED: "#4caf50",
    ClaimStatus.DOWNLOAD_FAILED: "#f44336",
    ClaimStatus.REVIEW_PENDING: "#ff9800",
    ClaimStatus.REVIEWING: "#2196f3",
    ClaimStatus.REVIEWED: "#4caf50",
    ClaimStatus.SUPPLEMENTARY_NEEDED: "#ff9800",
    ClaimStatus.PENDING_SUPPLEMENTARY: "#ff9800",
    ClaimStatus.SUPPLEMENTARY_RECEIVED: "#4caf50",
    ClaimStatus.APPROVED: "#4caf50",
    ClaimStatus.REJECTED: "#f44336",
    ClaimStatus.COMPLETED: "#9e9e9e",  # 灰色
    ClaimStatus.ERROR: "#f44336",
    ClaimStatus.MAX_RETRIES_EXCEEDED: "#f44336",
}

# 状态图标映射（用于UI显示）
STATUS_ICONS = {
    # 下载状态
    DownloadStatus.PENDING: "⏳",
    DownloadStatus.DOWNLOADING: "⬇️",
    DownloadStatus.COMPLETED: "✅",
    DownloadStatus.FAILED: "❌",
    DownloadStatus.RETRYING: "🔄",

    # 审核状态
    ReviewStatus.PENDING: "⏳",
    ReviewStatus.PROCESSING: "🔍",
    ReviewStatus.COMPLETED: "✅",
    ReviewStatus.FAILED: "❌",
    ReviewStatus.SUPPLEMENTARY_NEEDED: "📄",

    # 补件状态
    SupplementaryStatus.PENDING: "⏳",
    SupplementaryStatus.REQUESTED: "📤",
    SupplementaryStatus.RECEIVED: "📥",
    SupplementaryStatus.VERIFIED: "✅",
    SupplementaryStatus.TIMEOUT: "⏰",
    SupplementaryStatus.REJECTED: "❌",

    # 整体状态
    ClaimStatus.DOWNLOAD_PENDING: "⏳",
    ClaimStatus.DOWNLOADING: "⬇️",
    ClaimStatus.DOWNLOADED: "✅",
    ClaimStatus.DOWNLOAD_FAILED: "❌",
    ClaimStatus.REVIEW_PENDING: "⏳",
    ClaimStatus.REVIEWING: "🔍",
    ClaimStatus.REVIEWED: "✅",
    ClaimStatus.SUPPLEMENTARY_NEEDED: "📄",
    ClaimStatus.PENDING_SUPPLEMENTARY: "⏳",
    ClaimStatus.SUPPLEMENTARY_RECEIVED: "📥",
    ClaimStatus.APPROVED: "✅",
    ClaimStatus.REJECTED: "❌",
    ClaimStatus.COMPLETED: "🏁",
    ClaimStatus.ERROR: "⚠️",
    ClaimStatus.MAX_RETRIES_EXCEEDED: "🚫",
}