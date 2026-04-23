#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库模型定义
生产化系统所需的数据表模型 - 整合所有审核字段
"""

import json
from datetime import datetime, date
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from enum import Enum


class ClaimStatus(str, Enum):
    """案件状态枚举"""
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    DOWNLOAD_FAILED = "download_failed"
    REVIEW_PENDING = "review_pending"
    REVIEWING = "reviewing"
    REVIEWED = "reviewed"
    SUPPLEMENTARY_NEEDED = "supplementary_needed"
    PENDING_SUPPLEMENTARY = "pending_supplementary"
    SUPPLEMENTARY_RECEIVED = "supplementary_received"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
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


@dataclass
class ClaimStatusRecord:
    """案件状态记录"""
    id: Optional[int] = None
    claim_id: str = ""
    forceid: str = ""
    claim_type: str = ""
    current_status: str = ClaimStatus.DOWNLOAD_PENDING
    previous_status: Optional[str] = None
    status_changed_at: datetime = field(default_factory=datetime.now)
    download_status: str = DownloadStatus.PENDING
    download_attempts: int = 0
    last_download_time: Optional[datetime] = None
    review_status: str = ReviewStatus.PENDING
    review_attempts: int = 0
    last_review_time: Optional[datetime] = None
    supplementary_count: int = 0
    max_supplementary: int = 3
    next_check_time: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ClaimStatusRecord':
        """从字典创建"""
        datetime_fields = [
            'status_changed_at', 'last_download_time', 'last_review_time',
            'next_check_time', 'created_at', 'updated_at'
        ]
        for field_name in datetime_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
                    except ValueError:
                        data[field_name] = None
        return cls(**data)


@dataclass
class ReviewResult:
    """审核结果记录 - 整合所有审核字段"""
    # 基础信息
    id: Optional[int] = None
    forceid: str = ""
    claim_id: Optional[str] = None

    # 被保险人信息
    passenger_name: Optional[str] = None
    passenger_id_type: Optional[str] = None
    passenger_id_number: Optional[str] = None

    # 保单信息
    policy_no: Optional[str] = None
    insurer: Optional[str] = None
    policy_effective_date: Optional[date] = None
    policy_expiry_date: Optional[date] = None

    # 航班信息
    flight_no: Optional[str] = None
    operating_carrier: Optional[str] = None
    dep_iata: Optional[str] = None
    arr_iata: Optional[str] = None
    dep_city: Optional[str] = None
    arr_city: Optional[str] = None
    dep_country: Optional[str] = None
    arr_country: Optional[str] = None

    # 航班时间（原航班）
    planned_dep_time: Optional[datetime] = None   # 原航班首次购票计划起飞（schedule_local）
    actual_dep_time: Optional[datetime] = None    # 原航班飞常准实际起飞
    planned_arr_time: Optional[datetime] = None   # 原航班首次购票计划到达
    actual_arr_time: Optional[datetime] = None    # 原航班飞常准实际到达

    # 实际乘坐航班时间（改签/替代）
    alt_dep_time: Optional[datetime] = None       # 被保险人最终乘坐航班实际起飞
    alt_arr_time: Optional[datetime] = None       # 被保险人最终乘坐航班实际到达

    # 行李延误专属字段
    baggage_receipt_time: Optional[datetime] = None   # 行李签收时间（延误终止点）
    baggage_delay_hours: Optional[float] = None       # 行李延误小时数
    has_baggage_delay_proof: Optional[str] = None     # 是否有行李延误证明 Y/N
    has_baggage_receipt_proof: Optional[str] = None   # 是否有签收时间证明 Y/N
    has_baggage_tag_proof: Optional[str] = None       # 是否有行李牌 Y/N
    pir_no: Optional[str] = None                      # PIR不正常行李报告编号

    # 航班场景
    flight_scenario: Optional[str] = None        # direct/connecting/rebooking/multi_rebooking/cancelled_nofly
    rebooking_count: Optional[int] = None        # 改签次数（0=无改签）

    # 实际乘坐航班信息
    alt_flight_no: Optional[str] = None          # 被保险人实际乘坐的改签航班号
    alt_dep_iata: Optional[str] = None           # 实际乘坐航班出发机场
    alt_arr_iata: Optional[str] = None           # 实际乘坐航班到达机场

    # 联程信息（汇总标量，详情见 ai_review_segments 子表）
    # flight_no/dep_iata/arr_iata 始终记录"触发延误的那段"
    is_connecting: Optional[bool] = None         # 是否联程（True=联程，False=直飞）
    total_segments: Optional[int] = None         # 联程总段数（直飞=1）
    origin_iata: Optional[str] = None            # 整个行程出发机场（联程首段起飞地）
    destination_iata: Optional[str] = None       # 整个行程最终目的地（联程末段落地）
    missed_connection: Optional[bool] = None     # 是否因前段延误导致误机（联程接驳失误）

    # 飞常准查原航班
    avi_status: Optional[str] = None             # 飞常准原航班状态（正常/延误/取消）
    avi_planned_dep: Optional[datetime] = None   # 飞常准原航班计划起飞
    avi_planned_arr: Optional[datetime] = None   # 飞常准原航班计划到达
    avi_actual_dep: Optional[datetime] = None    # 飞常准原航班实际起飞
    avi_actual_arr: Optional[datetime] = None    # 飞常准原航班实际到达

    # 飞常准查替代航班
    avi_alt_flight_no: Optional[str] = None      # 飞常准查到的替代航班号
    avi_alt_planned_dep: Optional[datetime] = None
    avi_alt_actual_dep: Optional[datetime] = None
    avi_alt_actual_arr: Optional[datetime] = None

    # 延误计算追溯
    delay_calc_from: Optional[str] = None        # 延误起算时间点来源字段名
    delay_calc_to: Optional[str] = None          # 延误终止时间点来源字段名

    # 延误计算
    delay_duration_minutes: Optional[int] = None
    delay_reason: Optional[str] = None
    delay_type: Optional[str] = None

    # 审核结果
    audit_result: Optional[str] = None
    audit_status: str = "pending"
    confidence_score: Optional[float] = None
    audit_time: Optional[datetime] = None
    auditor: str = "AI系统"

    # 赔付信息
    payout_amount: Optional[float] = None
    payout_currency: str = "CNY"
    payout_basis: Optional[str] = None
    insured_amount: Optional[float] = None
    remaining_coverage: Optional[float] = None

    # 补件信息
    is_additional: str = "N"
    supplementary_count: int = 0
    supplementary_reason: Optional[str] = None
    supplementary_deadline: Optional[datetime] = None

    # 审核结论
    remark: Optional[str] = None
    key_conclusions: Optional[str] = None
    final_decision: Optional[str] = None
    decision_reason: Optional[str] = None
    review_status: Optional[str] = None
    benefit_name: Optional[str] = None

    # 逻辑校验
    identity_match: Optional[str] = None
    threshold_met: Optional[str] = None
    exclusion_triggered: Optional[str] = None
    exclusion_reason: Optional[str] = None

    # 前端推送状态
    forwarded_to_frontend: bool = False
    forwarded_at: Optional[datetime] = None
    frontend_response: Optional[str] = None

    # 原始数据
    raw_result: Optional[str] = None

    # 元数据
    metadata: Optional[Dict[str, Any]] = None

    # 时间戳
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        # 处理datetime字段
        datetime_fields = [
            'planned_dep_time', 'actual_dep_time', 'planned_arr_time', 'actual_arr_time',
            'alt_dep_time', 'alt_arr_time', 'baggage_receipt_time',
            'avi_planned_dep', 'avi_planned_arr', 'avi_actual_dep', 'avi_actual_arr',
            'avi_alt_planned_dep', 'avi_alt_actual_dep', 'avi_alt_actual_arr',
            'audit_time', 'supplementary_deadline',
            'forwarded_at', 'created_at', 'updated_at'
        ]
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, date):
                data[key] = value.isoformat()

        # 处理JSON字段
        if data.get('metadata') is not None:
            data['metadata'] = json.dumps(data['metadata'], ensure_ascii=False) if isinstance(data['metadata'], dict) else data['metadata']

        # 排除表中不存在的字段
        data.pop('review_status', None)

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReviewResult':
        """从字典创建"""
        # 处理datetime字段
        datetime_fields = [
            'planned_dep_time', 'actual_dep_time', 'planned_arr_time', 'actual_arr_time',
            'alt_dep_time', 'alt_arr_time', 'baggage_receipt_time',
            'avi_planned_dep', 'avi_planned_arr', 'avi_actual_dep', 'avi_actual_arr',
            'avi_alt_planned_dep', 'avi_alt_actual_dep', 'avi_alt_actual_arr',
            'audit_time', 'supplementary_deadline',
            'forwarded_at', 'created_at', 'updated_at'
        ]
        for field_name in datetime_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
                    except ValueError:
                        data[field_name] = None

        # 处理date字段
        date_fields = ['policy_effective_date', 'policy_expiry_date']
        for field_name in date_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = date.fromisoformat(data[field_name])
                    except ValueError:
                        data[field_name] = None

        # 处理JSON字段
        if 'metadata' in data and data['metadata']:
            if isinstance(data['metadata'], str):
                try:
                    data['metadata'] = json.loads(data['metadata'])
                except json.JSONDecodeError:
                    data['metadata'] = {}

        # 过滤掉dataclass不认识的字段
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in data.items() if k in known}

        return cls(**data)


@dataclass
class SupplementaryRecord:
    """补件记录"""
    id: Optional[int] = None
    claim_id: str = ""
    forceid: str = ""
    supplementary_number: int = 1
    requested_at: datetime = field(default_factory=datetime.now)
    requested_reason: str = ""
    required_materials: List[str] = field(default_factory=list)
    deadline: datetime = field(default_factory=lambda: datetime.now())
    completed_at: Optional[datetime] = None
    completed_materials: Optional[List[str]] = None
    status: str = SupplementaryStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        # 始终序列化 list 字段，空列表也序列化为 "[]"，避免直接传 Python list 给 MySQL
        data['required_materials'] = json.dumps(self.required_materials or [], ensure_ascii=False)
        data['completed_materials'] = json.dumps(self.completed_materials, ensure_ascii=False) if self.completed_materials else None
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SupplementaryRecord':
        """从字典创建"""
        datetime_fields = ['requested_at', 'deadline', 'completed_at', 'created_at', 'updated_at']
        for field_name in datetime_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
                    except ValueError:
                        data[field_name] = None

        if 'required_materials' in data and data['required_materials']:
            if isinstance(data['required_materials'], str):
                try:
                    data['required_materials'] = json.loads(data['required_materials'])
                except json.JSONDecodeError:
                    data['required_materials'] = []

        if 'completed_materials' in data and data['completed_materials']:
            if isinstance(data['completed_materials'], str):
                try:
                    data['completed_materials'] = json.loads(data['completed_materials'])
                except json.JSONDecodeError:
                    data['completed_materials'] = []

        return cls(**data)


@dataclass
class SchedulerLog:
    """定时任务日志"""
    id: Optional[int] = None
    task_type: str = TaskType.DOWNLOAD
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    status: str = TaskStatus.PENDING
    processed_count: int = 0
    success_count: int = 0
    failed_count: int = 0
    error_message: Optional[str] = None
    duration_seconds: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        if self.end_time and self.start_time:
            data['duration_seconds'] = int((self.end_time - self.start_time).total_seconds())
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SchedulerLog':
        """从字典创建"""
        datetime_fields = ['start_time', 'end_time', 'created_at']
        for field_name in datetime_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
                    except ValueError:
                        data[field_name] = None
        return cls(**data)


@dataclass
class StatusHistory:
    """状态变更历史"""
    id: Optional[int] = None
    claim_id: str = ""
    forceid: str = ""
    from_status: Optional[str] = None
    to_status: str = ""
    changed_by: str = "system"
    change_reason: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StatusHistory':
        """从字典创建"""
        if 'created_at' in data and data['created_at']:
            if isinstance(data['created_at'], str):
                try:
                    data['created_at'] = datetime.fromisoformat(data['created_at'].replace('Z', '+00:00'))
                except ValueError:
                    data['created_at'] = datetime.now()
        return cls(**data)


@dataclass
class ClaimInfoRaw:
    """案件原始下载信息（claim_info.json 落库备份）"""
    id: Optional[int] = None

    forceid: str = ""
    claim_id: Optional[str] = None

    benefit_name: Optional[str] = None
    applicant_name: Optional[str] = None

    # 来自 samePolicyClaim 的被保险人信息
    insured_name: Optional[str] = None
    id_type: Optional[str] = None
    id_number: Optional[str] = None
    birthday: Optional[date] = None
    gender: Optional[str] = None

    # 保单信息（samePolicyClaim）
    policy_no: Optional[str] = None
    insurance_company: Optional[str] = None
    product_name: Optional[str] = None
    plan_name: Optional[str] = None
    effective_date: Optional[str] = None
    expiry_date: Optional[str] = None
    date_of_insurance: Optional[str] = None

    # 本案维度（camelCase 字段）
    case_insured_name: Optional[str] = None
    case_policy_no: Optional[str] = None
    case_insurance_company: Optional[str] = None
    case_effective_date: Optional[str] = None
    case_expiry_date: Optional[str] = None
    case_id_type: Optional[str] = None
    case_id_number: Optional[str] = None
    insured_amount: Optional[float] = None
    reserved_amount: Optional[float] = None
    remaining_coverage: Optional[float] = None
    claim_amount: Optional[float] = None

    # 事故信息
    date_of_accident: Optional[date] = None
    final_status: Optional[str] = None
    description_of_accident: Optional[str] = None

    source_date: Optional[str] = None

    raw_json: Optional[str] = None

    downloaded_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, date):
                data[key] = value.isoformat()
        return data


@dataclass
class ReviewSegment:
    """联程航段记录（ai_review_segments 子表，通过 forceid 关联 ai_review_result）"""
    id: Optional[int] = None
    forceid: str = ""                            # 关联主表 forceid

    # 票号与航段序号
    ticket_no: Optional[str] = None             # 票号（同一份行程单可能多票）
    segment_no: int = 1                          # 航段序号（1起算）

    # 航班号与航线
    flight_no: Optional[str] = None             # 本段航班号
    dep_iata: Optional[str] = None              # 本段起飞机场
    arr_iata: Optional[str] = None              # 本段到达机场
    origin_iata: Optional[str] = None           # 全程始发地（冗余，方便查询）
    destination_iata: Optional[str] = None      # 全程目的地（冗余，方便查询）

    # 计划时间（材料/保单）
    planned_dep: Optional[datetime] = None      # 计划起飞
    planned_arr: Optional[datetime] = None      # 计划到达

    # 实际时间（飞常准）
    actual_dep: Optional[datetime] = None       # 飞常准实际起飞
    actual_arr: Optional[datetime] = None       # 飞常准实际到达

    # 延误计算
    delay_min: Optional[int] = None             # 本段延误分钟（actual_dep - planned_dep）
    avi_status: Optional[str] = None            # 飞常准航班状态（正常/延误/取消）

    # 标志位
    is_triggered: Optional[bool] = None         # 是否为触发延误险赔付的那段（1=是）
    is_connecting: Optional[bool] = None        # 是否联程（与主表一致，冗余方便按段查询）
    missed_connect: Optional[bool] = None       # 本段是否因前段延误而误机

    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReviewSegment':
        datetime_fields = ['planned_dep', 'planned_arr', 'actual_dep', 'actual_arr', 'created_at']
        for field_name in datetime_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
                    except ValueError:
                        data[field_name] = None
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        return cls(**data)


# 数据库表名常量
TABLE_CLAIM_STATUS = "ai_claim_status"
TABLE_REVIEW_RESULT = "ai_review_result"
TABLE_REVIEW_SEGMENTS = "ai_review_segments"
TABLE_SUPPLEMENTARY_RECORDS = "ai_supplementary_records"
TABLE_SCHEDULER_LOGS = "ai_scheduler_logs"
TABLE_STATUS_HISTORY = "ai_status_history"
TABLE_CLAIM_INFO_RAW = "ai_claim_info_raw"