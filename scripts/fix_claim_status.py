#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 ai_claim_status 表中状态不一致的记录，
使这些案件能被生产系统重新拾取并审核。
"""

import os
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import pymysql

conn = pymysql.connect(
    host=os.getenv("DB_HOST", ""),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", ""),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "ai"),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

# 目标案件列表（需要重新审核的）
NEED_REAUDIT = [
    "a0nC800000LOW4LIAX",  # current=reviewing, review=failed
    "a0nC800000LP3xuIAD",  # current=reviewing, review=failed
    "a0nC800000LPJY7IAP",  # current=error, review=failed
    "a0nC800000LQmPuIAL",  # current=supplementary_needed, review=failed
    "a0nC800000LR0MPIA1",  # current=downloaded, review=pending — 只需确保next_check_time过去
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
    # 将所有 review_status=failed 的案件重置为 downloaded/pending 状态，让系统重新审核
    for forceid in NEED_REAUDIT:
        if forceid == "a0nC800000LR0MPIA1":
            # 这个案件已经是 downloaded，只需确保 next_check_time 为当前时间之前
            cur.execute(
                """UPDATE ai_claim_status SET
                   review_status = 'pending',
                   next_check_time = %s,
                   error_message = NULL,
                   updated_at = %s
                   WHERE forceid = %s""",
                (datetime(2026, 1, 1), datetime.now(), forceid)
            )
        else:
            # 重置为 downloaded 状态，让系统重新执行审核
            cur.execute(
                """UPDATE ai_claim_status SET
                   current_status = 'downloaded',
                   review_status = 'pending',
                   next_check_time = %s,
                   error_message = NULL,
                   updated_at = %s
                   WHERE forceid = %s""",
                (datetime(2026, 1, 1), datetime.now(), forceid)
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
