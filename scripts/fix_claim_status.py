#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
修复 ai_claim_status 表中状态不一致的记录，
使这些案件能被生产系统重新拾取并审核。
"""

import os
import sys
import pymysql
from datetime import datetime

# 读取 .env 文件
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

conn = pymysql.connect(
    host=os.environ.get("DB_HOST", ""),
    port=int(os.environ.get("DB_PORT", "3306")),
    user=os.environ.get("DB_USER", ""),
    password=os.environ.get("DB_PASSWORD", ""),
    database=os.environ.get("DB_NAME", "ai"),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

# 目标案件列表（需要重新审核的）
NEED_REAUDIT = [
    "a0nC800000LOW4LIAX",
    "a0nC800000LP3xuIAD",
    "a0nC800000LPJY7IAP",
    "a0nC800000LQmPuIAL",
    "a0nC800000LR0MPIA1",
]

print("=== 修复前状态 ===")
with conn.cursor() as cur:
    cur.execute("SELECT forceid, current_status, download_status, review_status, next_check_time, error_message FROM ai_claim_status ORDER BY created_at")
    for row in cur.fetchall():
        fid = row["forceid"]
        cs = row["current_status"]
        ds = row["download_status"]
        rs = row["review_status"]
        nc = row["next_check_time"]
        print("  %s: current=%s, download=%s, review=%s, next_check=%s" % (fid, cs, ds, rs, nc))

print("\n=== 执行修复 ===")
with conn.cursor() as cur:
    for forceid in NEED_REAUDIT:
        cur.execute(
            """UPDATE ai_claim_status SET
               current_status = 'downloaded',
               review_status = 'pending',
               next_check_time = '2026-01-01 00:00:00',
               error_message = NULL,
               updated_at = NOW()
               WHERE forceid = %s""",
            (forceid,)
        )
        print("  已重置: %s" % forceid)

conn.commit()

print("\n=== 修复后状态 ===")
with conn.cursor() as cur:
    cur.execute("SELECT forceid, current_status, download_status, review_status, next_check_time, error_message FROM ai_claim_status ORDER BY created_at")
    for row in cur.fetchall():
        fid = row["forceid"]
        cs = row["current_status"]
        ds = row["download_status"]
        rs = row["review_status"]
        nc = row["next_check_time"]
        print("  %s: current=%s, download=%s, review=%s, next_check=%s" % (fid, cs, ds, rs, nc))

conn.close()
print("\n修复完成！")
