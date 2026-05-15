#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审核历史版本追踪 — 同步辅助函数

供 pymysql 连接的所有写入代码路径调用。
每个函数接受一个 pymysql connection，在 UPSERT/UPDATE 后检测变化并写入历史。
"""

import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 追踪的 AI 字段列表
TRACKED_AI_FIELDS = [
    'audit_result', 'audit_status', 'confidence_score', 'payout_amount',
    'identity_match', 'threshold_met', 'exclusion_triggered',
]

TABLE_HISTORY = "ai_review_history"
TABLE_REVIEW_RESULT = "ai_review_result"


def _safe_value(v):
    """将值转为数据库安全的格式（None 保持 None）"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    return str(v)


def capture_existing_values(conn, forceid: str) -> Optional[Dict[str, Any]]:
    """读取当前行的追踪字段值，不存在返回 None"""
    fields_str = ', '.join(TRACKED_AI_FIELDS) + ', manual_status, manual_conclusion, first_ai_audit_time'
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {fields_str} FROM {TABLE_REVIEW_RESULT} WHERE forceid=%s",
            (forceid,)
        )
        row = cur.fetchone()
    return row


def insert_history_row(conn, record: Dict[str, Any]) -> int:
    """向 ai_review_history 插入一行"""
    keys = [k for k in record.keys() if k != 'id']
    placeholders = ', '.join(['%s'] * len(keys))
    values = [_safe_value(record.get(k)) for k in keys]
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TABLE_HISTORY} ({', '.join(keys)}) VALUES ({placeholders})",
            values
        )
        return cur.lastrowid


def _fields_differ(old: Optional[Dict], new: Dict, fields: list) -> bool:
    """比较新旧值是否有差异"""
    if old is None:
        return True  # 首次插入
    for f in fields:
        old_val = old.get(f)
        new_val = new.get(f)
        # 处理数值比较（Decimal vs float）
        if old_val is None and new_val is None:
            continue
        if old_val is None or new_val is None:
            return True
        # 统一转为字符串比较避免类型差异
        if str(old_val).strip() != str(new_val).strip():
            return True
    return False


def write_ai_history_if_changed(conn, forceid: str, new_fields: Dict[str, Any],
                                existing: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """UPSERT 成功后调用，检测 AI 字段变化并写入历史行。

    Args:
        conn: pymysql connection（已开启事务）
        forceid: 案件唯一ID
        new_fields: 刚刚 UPSERT 的字段字典
        existing: 可选，调用方已读取的旧值（避免重复查询）。
                  如果为 None，本函数会重新查询。

    Returns:
        历史行 ID（有变化时）或 None（无变化）
    """
    if existing is None:
        existing = capture_existing_values(conn, forceid)

    if not _fields_differ(existing, new_fields, TRACKED_AI_FIELDS):
        return None

    # 构建历史记录
    history = {
        'forceid': forceid,
        'claim_id': new_fields.get('claim_id'),
        'benefit_name': new_fields.get('benefit_name'),
        'review_type': 'ai',
        'audit_result': new_fields.get('audit_result'),
        'audit_status': new_fields.get('audit_status'),
        'confidence_score': new_fields.get('confidence_score'),
        'payout_amount': new_fields.get('payout_amount'),
        'identity_match': new_fields.get('identity_match'),
        'threshold_met': new_fields.get('threshold_met'),
        'exclusion_triggered': new_fields.get('exclusion_triggered'),
        'manual_status': existing.get('manual_status') if existing else None,
        'manual_conclusion': existing.get('manual_conclusion') if existing else None,
        'ai_model_version': new_fields.get('ai_model_version'),
        'pipeline_version': new_fields.get('pipeline_version'),
        'rule_ids_hit': new_fields.get('rule_ids_hit'),
        'audit_time': new_fields.get('audit_time'),
    }

    # snapshot_json: 完整快照
    snapshot = {k: _safe_value(v) for k, v in new_fields.items()}
    history['snapshot_json'] = json.dumps(snapshot, ensure_ascii=False, default=str)

    row_id = insert_history_row(conn, history)

    # 设置里程碑列（仅首次）
    is_first = existing is None or existing.get('first_ai_audit_time') is None
    if is_first:
        _set_milestone_columns(conn, forceid, new_fields, existing)

    logger.debug(f"历史版本记录: forceid={forceid}, history_id={row_id}, type=ai")
    return row_id


def write_manual_history_if_changed(conn, forceid: str,
                                     new_manual_status: str,
                                     new_manual_conclusion: Optional[str] = None,
                                     benefit_name: Optional[str] = None,
                                     old_values: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """UPDATE 人工状态后调用，检测变化并写入历史行。

    Args:
        conn: pymysql connection（已开启事务）
        forceid: 案件唯一ID
        new_manual_status: 新的人工状态
        new_manual_conclusion: 新的人工结论
        benefit_name: 险种名称（可选）
        old_values: 可选，调用方已读取的旧值 {'manual_status': ..., 'manual_conclusion': ..., ...}
                    如果为 None，本函数会重新查询（注意：查询结果为 UPDATE 后的新值）。

    Returns:
        历史行 ID（有变化时）或 None（无变化）
    """
    if old_values is not None:
        existing = old_values
    else:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT manual_status, manual_conclusion, {', '.join(TRACKED_AI_FIELDS)} "
                f"FROM {TABLE_REVIEW_RESULT} WHERE forceid=%s",
                (forceid,)
            )
            existing = cur.fetchone()

    if existing is None:
        return None

    old_status = existing.get('manual_status')
    old_conclusion = existing.get('manual_conclusion')

    if str(old_status or '').strip() == str(new_manual_status or '').strip() and \
       str(old_conclusion or '').strip() == str(new_manual_conclusion or '').strip():
        return None  # 无变化

    # 构建历史记录（同时保存当前 AI 状态作为快照上下文）
    history = {
        'forceid': forceid,
        'benefit_name': benefit_name,
        'review_type': 'manual',
        'manual_status': new_manual_status,
        'manual_conclusion': new_manual_conclusion,
        'audit_result': existing.get('audit_result'),
        'audit_status': existing.get('audit_status'),
        'confidence_score': existing.get('confidence_score'),
        'payout_amount': existing.get('payout_amount'),
        'identity_match': existing.get('identity_match'),
        'threshold_met': existing.get('threshold_met'),
        'exclusion_triggered': existing.get('exclusion_triggered'),
    }
    history['snapshot_json'] = json.dumps({
        'forceid': forceid,
        'manual_status': new_manual_status,
        'manual_conclusion': new_manual_conclusion,
        'ai_audit_result': existing.get('audit_result'),
        'ai_payout_amount': str(existing.get('payout_amount')),
    }, ensure_ascii=False)

    row_id = insert_history_row(conn, history)
    logger.debug(f"历史版本记录: forceid={forceid}, history_id={row_id}, type=manual")
    return row_id


def _set_milestone_columns(conn, forceid: str, new_fields: Dict[str, Any],
                           existing: Optional[Dict[str, Any]]) -> None:
    """设置主表的 first_ai_* 里程碑列（仅首次调用时执行）"""
    # 读取当前最新的人工状态（UPSERT 后的值）
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT manual_status, manual_conclusion FROM {TABLE_REVIEW_RESULT} WHERE forceid=%s",
            (forceid,)
        )
        current = cur.fetchone()

    manual_status = (current or {}).get('manual_status')
    manual_conclusion = (current or {}).get('manual_conclusion')

    with conn.cursor() as cur:
        cur.execute(
            f"""UPDATE {TABLE_REVIEW_RESULT} SET
                first_ai_audit_result=%s,
                first_ai_audit_status=%s,
                first_ai_audit_time=%s,
                first_ai_confidence=%s,
                first_ai_manual_status=%s,
                first_ai_manual_conclusion=%s
                WHERE forceid=%s""",
            (
                new_fields.get('audit_result'),
                new_fields.get('audit_status'),
                new_fields.get('audit_time'),
                new_fields.get('confidence_score'),
                manual_status,
                manual_conclusion,
                forceid,
            )
        )
    logger.debug(f"里程碑列已设置: forceid={forceid}")
