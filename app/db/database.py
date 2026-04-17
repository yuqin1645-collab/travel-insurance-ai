#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库连接和操作类
生产化系统的数据库访问层 - 适配整合后的表结构
"""

import os
import json
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta, date
from contextlib import asynccontextmanager

import aiomysql
from app.config import config
from app.db.models import (
    ClaimStatusRecord, ReviewResult, SupplementaryRecord, SchedulerLog, StatusHistory,
    ClaimStatus, DownloadStatus, ReviewStatus, SupplementaryStatus, TaskType, TaskStatus,
    TABLE_CLAIM_STATUS, TABLE_REVIEW_RESULT, TABLE_SUPPLEMENTARY_RECORDS,
    TABLE_SCHEDULER_LOGS, TABLE_STATUS_HISTORY
)

LOGGER = logging.getLogger(__name__)


class DatabaseError(Exception):
    """数据库错误"""
    pass


class DatabaseConnection:
    """数据库连接管理"""

    def __init__(self):
        self.pool = None

    async def initialize(self):
        """初始化数据库连接池"""
        try:
            self.pool = await aiomysql.create_pool(
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWORD,
                db=config.DB_NAME,
                charset='utf8mb4',
                autocommit=True,
                maxsize=10,
                minsize=1,
                pool_recycle=3600
            )
            LOGGER.info("数据库连接池初始化成功")
        except Exception as e:
            LOGGER.error(f"数据库连接池初始化失败: {e}")
            raise DatabaseError(f"数据库连接失败: {e}")

    async def close(self):
        """关闭数据库连接池"""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()
            LOGGER.info("数据库连接池已关闭")

    @asynccontextmanager
    async def get_connection(self):
        """获取数据库连接"""
        if not self.pool:
            await self.initialize()
        conn = await self.pool.acquire()
        try:
            yield conn
        finally:
            self.pool.release(conn)


class ClaimStatusDAO:
    """案件状态数据访问对象"""

    def __init__(self, db: DatabaseConnection):
        self.db = db

    async def create_or_update_status(self, status_record: ClaimStatusRecord) -> int:
        """创建或更新案件状态（原子操作，防止并发竞态导致重复插入）"""
        now = datetime.now()
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                # 使用 INSERT ... ON DUPLICATE KEY UPDATE，对 forceid 和 claim_id 两个唯一键均幂等
                await cursor.execute(
                    f"""INSERT INTO {TABLE_CLAIM_STATUS}
                        (claim_id, forceid, claim_type, current_status, previous_status, status_changed_at,
                        download_status, download_attempts, last_download_time, review_status, review_attempts,
                        last_review_time, supplementary_count, max_supplementary, next_check_time, error_message,
                        created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        claim_id = VALUES(claim_id),
                        claim_type = VALUES(claim_type),
                        current_status = VALUES(current_status),
                        previous_status = VALUES(previous_status),
                        status_changed_at = VALUES(status_changed_at),
                        download_status = VALUES(download_status),
                        download_attempts = VALUES(download_attempts),
                        last_download_time = VALUES(last_download_time),
                        review_status = VALUES(review_status),
                        review_attempts = VALUES(review_attempts),
                        last_review_time = VALUES(last_review_time),
                        supplementary_count = VALUES(supplementary_count),
                        max_supplementary = VALUES(max_supplementary),
                        next_check_time = VALUES(next_check_time),
                        error_message = VALUES(error_message),
                        updated_at = VALUES(updated_at)""",
                    (
                        status_record.claim_id, status_record.forceid, status_record.claim_type,
                        status_record.current_status, status_record.previous_status, status_record.status_changed_at,
                        status_record.download_status, status_record.download_attempts, status_record.last_download_time,
                        status_record.review_status, status_record.review_attempts, status_record.last_review_time,
                        status_record.supplementary_count, status_record.max_supplementary, status_record.next_check_time,
                        status_record.error_message, now, now
                    )
                )
                return cursor.lastrowid

    async def update_current_status(self, forceid: str, new_status, change_reason: Optional[str] = None) -> bool:
        """更新案件当前状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {TABLE_CLAIM_STATUS} SET current_status = %s, status_changed_at = %s, updated_at = %s WHERE forceid = %s",
                    (str(new_status.value if hasattr(new_status, 'value') else new_status), datetime.now(), datetime.now(), forceid)
                )
                return cursor.rowcount > 0

    async def get_status_by_forceid(self, forceid: str) -> Optional[ClaimStatusRecord]:
        """根据forceid获取案件状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"SELECT * FROM {TABLE_CLAIM_STATUS} WHERE forceid = %s", (forceid,)
                )
                row = await cursor.fetchone()
                return ClaimStatusRecord.from_dict(row) if row else None

    async def get_status_by_claim_id(self, claim_id: str) -> Optional[ClaimStatusRecord]:
        """根据 claim_id 获取案件状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"SELECT * FROM {TABLE_CLAIM_STATUS} WHERE claim_id = %s", (claim_id,)
                )
                row = await cursor.fetchone()
                return ClaimStatusRecord.from_dict(row) if row else None
    async def get_pending_downloads(self, limit: int = 10) -> List[ClaimStatusRecord]:
        """获取待下载的案件（仅限尚未下载完成的案件）"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"""SELECT * FROM {TABLE_CLAIM_STATUS}
                        WHERE current_status IN (%s, %s, %s)
                        AND (next_check_time IS NULL OR next_check_time <= %s)
                        ORDER BY created_at ASC LIMIT %s""",
                    (ClaimStatus.DOWNLOAD_PENDING, ClaimStatus.DOWNLOADING, ClaimStatus.DOWNLOAD_FAILED, datetime.now(), limit)
                )
                rows = await cursor.fetchall()
                return [ClaimStatusRecord.from_dict(row) for row in rows]

    async def get_pending_reviews(self, limit: int = 10) -> List[ClaimStatusRecord]:
        """获取待审核的案件（仅限已下载但尚未开始审核的案件）"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"""SELECT * FROM {TABLE_CLAIM_STATUS}
                        WHERE current_status IN (%s, %s)
                        AND (next_check_time IS NULL OR next_check_time <= %s)
                        ORDER BY created_at ASC LIMIT %s""",
                    (ClaimStatus.DOWNLOADED, ClaimStatus.REVIEW_PENDING, datetime.now(), limit)
                )
                rows = await cursor.fetchall()
                return [ClaimStatusRecord.from_dict(row) for row in rows]

    async def update_download_status(
        self, forceid: str, download_status: str, error_message: Optional[str] = None,
        next_check_time: Optional[datetime] = None
    ) -> bool:
        """更新下载状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"""UPDATE {TABLE_CLAIM_STATUS} SET
                        download_status = %s, download_attempts = download_attempts + 1,
                        last_download_time = %s, error_message = %s, next_check_time = %s, updated_at = %s
                        WHERE forceid = %s""",
                    (download_status, datetime.now(), error_message, next_check_time, datetime.now(), forceid)
                )
                return cursor.rowcount > 0

    async def update_review_status(
        self, forceid: str, review_status: str, supplementary_count: Optional[int] = None,
        error_message: Optional[str] = None, next_check_time: Optional[datetime] = None
    ) -> bool:
        """更新审核状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                update_fields = [
                    "review_status = %s", "review_attempts = review_attempts + 1",
                    "last_review_time = %s", "error_message = %s", "next_check_time = %s", "updated_at = %s"
                ]
                params = [review_status, datetime.now(), error_message, next_check_time, datetime.now(), forceid]
                if supplementary_count is not None:
                    update_fields.insert(0, "supplementary_count = %s")
                    params.insert(0, supplementary_count)
                sql = f"UPDATE {TABLE_CLAIM_STATUS} SET {', '.join(update_fields)} WHERE forceid = %s"
                await cursor.execute(sql, params)
                return cursor.rowcount > 0


class ReviewResultDAO:
    """审核结果数据访问对象 - 适配整合后的表结构"""

    def __init__(self, db: DatabaseConnection):
        self.db = db

    def _get_review_result_columns(self) -> List[str]:
        """获取审核结果表的列名"""
        return [
            'forceid', 'claim_id', 'passenger_name', 'passenger_id_type', 'passenger_id_number',
            'policy_no', 'insurer', 'policy_effective_date', 'policy_expiry_date',
            'flight_no', 'operating_carrier', 'dep_iata', 'arr_iata', 'dep_city', 'arr_city',
            'dep_country', 'arr_country', 'planned_dep_time', 'actual_dep_time',
            'planned_arr_time', 'actual_arr_time', 'alt_dep_time', 'alt_arr_time',
            'delay_duration_minutes', 'delay_reason', 'delay_type',
            'audit_result', 'audit_status', 'confidence_score', 'audit_time', 'auditor',
            'payout_amount', 'payout_currency', 'payout_basis', 'insured_amount', 'remaining_coverage',
            'is_additional', 'supplementary_count', 'supplementary_reason', 'supplementary_deadline',
            'remark', 'key_conclusions', 'decision_reason',
            'identity_match', 'threshold_met', 'exclusion_triggered', 'exclusion_reason',
            'forwarded_to_frontend', 'forwarded_at', 'frontend_response', 'raw_result', 'metadata'
        ]

    async def create_or_update_result(self, result: ReviewResult) -> int:
        """创建或更新审核结果"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"SELECT id FROM {TABLE_REVIEW_RESULT} WHERE forceid = %s", (result.forceid,)
                )
                existing = await cursor.fetchone()

                # 构建字段和值映射
                result_dict = result.to_dict()

                # 排除不需要的字段
                exclude_fields = ['id', 'created_at']
                fields_to_update = {k: v for k, v in result_dict.items() if k not in exclude_fields}

                if existing:
                    # 更新
                    update_fields = [f"{k} = %s" for k in fields_to_update.keys()]
                    update_fields.append("updated_at = %s")
                    params = list(fields_to_update.values())
                    params.append(datetime.now())
                    params.append(result.forceid)

                    sql = f"""UPDATE {TABLE_REVIEW_RESULT} SET {', '.join(update_fields)} WHERE forceid = %s"""
                    await cursor.execute(sql, params)
                    return existing[0]
                else:
                    # 插入
                    keys = list(fields_to_update.keys())
                    placeholders = ['%s'] * len(keys)
                    values = list(fields_to_update.values())

                    sql = f"""INSERT INTO {TABLE_REVIEW_RESULT} ({', '.join(keys)}) VALUES ({', '.join(placeholders)})"""
                    await cursor.execute(sql, values)
                    return cursor.lastrowid

    async def get_result_by_forceid(self, forceid: str) -> Optional[ReviewResult]:
        """根据forceid获取审核结果"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"SELECT * FROM {TABLE_REVIEW_RESULT} WHERE forceid = %s", (forceid,)
                )
                row = await cursor.fetchone()
                return ReviewResult.from_dict(row) if row else None

    async def update_frontend_status(
        self, forceid: str, forwarded: bool, response: Optional[str] = None
    ) -> bool:
        """更新前端推送状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"""UPDATE {TABLE_REVIEW_RESULT} SET
                        forwarded_to_frontend = %s, forwarded_at = %s, frontend_response = %s, updated_at = %s
                        WHERE forceid = %s""",
                    (forwarded, datetime.now() if forwarded else None, response, datetime.now(), forceid)
                )
                return cursor.rowcount > 0

    async def batch_update_from_json_files(self, results: List[Dict[str, Any]]) -> Tuple[int, int]:
        """批量从JSON文件更新审核结果（兼容旧数据）"""
        success = 0
        fail = 0

        for data in results:
            try:
                # 从JSON中提取字段
                forceid = data.get('forceid', '')
                if not forceid:
                    continue

                # 解析flight_delay_audit部分
                audit = data.get('flight_delay_audit', {})
                payout = audit.get('payout_suggestion', {})
                logic_check = audit.get('logic_check', {})

                # 解析DebugInfo
                debug_info = data.get('DebugInfo', {})
                flight_delay = debug_info.get('flight_delay', {}) or {}
                vision_extract = flight_delay.get('flight_delay_vision_extract', {}) or {}
                parse = flight_delay.get('flight_delay_parse', {}) or {}
                aviation = flight_delay.get('flight_delay_aviation_lookup', {}) or {}

                # 构建审核结果
                result = ReviewResult(
                    forceid=forceid,
                    claim_id=data.get('claim_id'),
                    remark=data.get('Remark', '')[:2000] if data.get('Remark') else None,
                    is_additional=data.get('IsAdditional', 'N'),
                    key_conclusions=json.dumps(data.get('KeyConclusions', []), ensure_ascii=False),
                    raw_result=json.dumps(data, ensure_ascii=False),
                    audit_result=audit.get('audit_result'),
                    audit_status='completed' if audit.get('audit_result') else 'pending',
                    confidence_score=audit.get('confidence_score'),
                    audit_time=datetime.now(),
                    payout_amount=payout.get('amount'),
                    payout_currency=payout.get('currency', 'CNY'),
                    payout_basis=payout.get('basis'),
                    delay_duration_minutes=audit.get('key_data', {}).get('delay_duration_minutes'),
                    delay_reason=audit.get('key_data', {}).get('reason'),
                    identity_match='Y' if logic_check.get('identity_match') else 'N',
                    threshold_met='Y' if logic_check.get('threshold_met') else 'N',
                    exclusion_triggered='Y' if logic_check.get('exclusion_triggered') else 'N',
                    passenger_name=parse.get('passenger', {}).get('name'),
                    passenger_id_type=parse.get('passenger', {}).get('id_type'),
                    passenger_id_number=parse.get('passenger', {}).get('id_number'),
                    policy_no=parse.get('policy_hint', {}).get('policy_no'),
                    insurer=parse.get('policy_hint', {}).get('insurer'),
                    flight_no=parse.get('flight', {}).get('ticket_flight_no') or parse.get('flight', {}).get('operating_flight_no'),
                    operating_carrier=parse.get('flight', {}).get('operating_carrier'),
                    dep_iata=parse.get('route', {}).get('dep_iata'),
                    arr_iata=parse.get('route', {}).get('arr_iata'),
                    dep_country=vision_extract.get('all_flights_found', [{}])[0].get('dep_iata') if vision_extract.get('all_flights_found') else None,
                    arr_country=vision_extract.get('all_flights_found', [{}])[0].get('arr_iata') if vision_extract.get('all_flights_found') else None,
                    planned_dep_time=self._parse_datetime(parse.get('schedule_local', {}).get('planned_dep')),
                    planned_arr_time=self._parse_datetime(parse.get('schedule_local', {}).get('planned_arr')),
                    actual_dep_time=self._parse_datetime(parse.get('actual_local', {}).get('actual_dep')),
                    alt_dep_time=self._parse_datetime(parse.get('alternate_local', {}).get('alt_dep')),
                    alt_arr_time=self._parse_datetime(parse.get('alternate_local', {}).get('alt_arr')),
                )

                await self.create_or_update_result(result)
                success += 1

            except Exception as e:
                LOGGER.error(f"更新审核结果失败: {data.get('forceid', '')}, 错误: {e}")
                fail += 1

        return success, fail

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        """解析日期时间"""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                return None
        return None


class SupplementaryDAO:
    """补件数据访问对象"""

    def __init__(self, db: DatabaseConnection):
        self.db = db

    async def create_supplementary_record(self, record: SupplementaryRecord) -> int:
        """创建补件记录"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                record_dict = record.to_dict()
                # 排除 id 字段（自增主键，不能显式插入 None）
                record_dict = {k: v for k, v in record_dict.items() if k != 'id'}
                keys = list(record_dict.keys())
                placeholders = ['%s'] * len(keys)
                values = list(record_dict.values())
                sql = f"INSERT INTO {TABLE_SUPPLEMENTARY_RECORDS} ({', '.join(keys)}) VALUES ({', '.join(placeholders)})"
                await cursor.execute(sql, values)
                return cursor.lastrowid

    async def get_supplementary_records(self, forceid: str) -> List[SupplementaryRecord]:
        """获取案件的补件记录"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"SELECT * FROM {TABLE_SUPPLEMENTARY_RECORDS} WHERE forceid = %s ORDER BY supplementary_number ASC",
                    (forceid,)
                )
                rows = await cursor.fetchall()
                return [SupplementaryRecord.from_dict(row) for row in rows]

    async def get_pending_supplementary(self, limit: int = 10) -> List[SupplementaryRecord]:
        """获取待处理的补件"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"""SELECT * FROM {TABLE_SUPPLEMENTARY_RECORDS}
                        WHERE status IN (%s, %s) AND deadline > %s ORDER BY deadline ASC LIMIT %s""",
                    (SupplementaryStatus.PENDING, SupplementaryStatus.REQUESTED, datetime.now(), limit)
                )
                rows = await cursor.fetchall()
                return [SupplementaryRecord.from_dict(row) for row in rows]

    async def get_expiring_supplementary(self, hours_before: int = 1) -> List[SupplementaryRecord]:
        """获取即将到期的补件"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                deadline_threshold = datetime.now() + timedelta(hours=hours_before)
                await cursor.execute(
                    f"""SELECT * FROM {TABLE_SUPPLEMENTARY_RECORDS}
                        WHERE status IN (%s, %s) AND deadline <= %s AND deadline > %s ORDER BY deadline ASC""",
                    (SupplementaryStatus.PENDING, SupplementaryStatus.REQUESTED, deadline_threshold, datetime.now())
                )
                rows = await cursor.fetchall()
                return [SupplementaryRecord.from_dict(row) for row in rows]

    async def update_supplementary_status(
        self, record_id: int, status: str, completed_materials: Optional[List[str]] = None
    ) -> bool:
        """更新补件状态"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                update_fields = ["status = %s", "updated_at = %s"]
                params = [status, datetime.now()]
                if status == SupplementaryStatus.RECEIVED:
                    update_fields.insert(0, "completed_at = %s")
                    params.insert(0, datetime.now())
                if completed_materials:
                    update_fields.insert(0, "completed_materials = %s")
                    params.insert(0, json.dumps(completed_materials, ensure_ascii=False))
                params.append(record_id)
                sql = f"UPDATE {TABLE_SUPPLEMENTARY_RECORDS} SET {', '.join(update_fields)} WHERE id = %s"
                await cursor.execute(sql, params)
                return cursor.rowcount > 0


class SchedulerLogDAO:
    """定时任务日志数据访问对象"""

    def __init__(self, db: DatabaseConnection):
        self.db = db

    async def create_log(self, log: SchedulerLog) -> int:
        """创建任务日志"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                log_dict = log.to_dict()
                keys = list(log_dict.keys())
                placeholders = ['%s'] * len(keys)
                values = list(log_dict.values())
                sql = f"INSERT INTO {TABLE_SCHEDULER_LOGS} ({', '.join(keys)}) VALUES ({', '.join(placeholders)})"
                await cursor.execute(sql, values)
                return cursor.lastrowid

    async def update_log(
        self, log_id: int, status: str, processed_count: int = 0, success_count: int = 0,
        failed_count: int = 0, error_message: Optional[str] = None
    ) -> bool:
        """更新任务日志"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"""UPDATE {TABLE_SCHEDULER_LOGS} SET
                        end_time = %s, status = %s, processed_count = %s, success_count = %s,
                        failed_count = %s, error_message = %s, duration_seconds = TIMESTAMPDIFF(SECOND, start_time, %s)
                        WHERE id = %s""",
                    (datetime.now(), status, processed_count, success_count, failed_count, error_message, datetime.now(), log_id)
                )
                return cursor.rowcount > 0

    async def get_recent_logs(self, task_type: Optional[str] = None, limit: int = 10) -> List[SchedulerLog]:
        """获取最近的任务日志"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                if task_type:
                    await cursor.execute(
                        f"SELECT * FROM {TABLE_SCHEDULER_LOGS} WHERE task_type = %s ORDER BY start_time DESC LIMIT %s",
                        (task_type, limit)
                    )
                else:
                    await cursor.execute(
                        f"SELECT * FROM {TABLE_SCHEDULER_LOGS} ORDER BY start_time DESC LIMIT %s", (limit,)
                    )
                rows = await cursor.fetchall()
                return [SchedulerLog.from_dict(row) for row in rows]


# 全局数据库连接实例
_db_connection = DatabaseConnection()


def get_db_connection() -> DatabaseConnection:
    """获取数据库连接实例"""
    return _db_connection


def get_claim_status_dao() -> ClaimStatusDAO:
    """获取案件状态DAO"""
    return ClaimStatusDAO(_db_connection)


def get_review_result_dao() -> ReviewResultDAO:
    """获取审核结果DAO"""
    return ReviewResultDAO(_db_connection)


def get_supplementary_dao() -> SupplementaryDAO:
    """获取补件DAO"""
    return SupplementaryDAO(_db_connection)


def get_scheduler_log_dao() -> SchedulerLogDAO:
    """获取任务日志DAO"""
    return SchedulerLogDAO(_db_connection)

