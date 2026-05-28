#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重审队列同步辅助函数

供 pymysql 连接的所有写入代码路径调用（如同步脚本）。
"""

from typing import Any


TABLE_RERUN_QUEUE = "ai_rerun_queue"


def enqueue_if_no_pending(conn: Any, forceid: str, triggered_by: str = "manual_status_change") -> int:
    """入队：如果同一 forceid 没有 pending/processing 状态的行，则 INSERT。

    Args:
        conn: pymysql connection（已开启事务）
        forceid: 案件唯一标识
        triggered_by: 触发来源

    Returns:
        新插入的行 ID；如果已有 pending/processing 行则返回 0
    """
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id FROM {TABLE_RERUN_QUEUE} "
            f"WHERE forceid = %s AND rerun_status IN ('pending', 'processing')",
            (forceid,)
        )
        existing = cur.fetchone()
        if existing:
            return 0  # 已存在，不重复入队

        cur.execute(
            f"INSERT INTO {TABLE_RERUN_QUEUE} (forceid, triggered_by) VALUES (%s, %s)",
            (forceid, triggered_by),
        )
        return cur.lastrowid
