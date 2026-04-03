#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查 ai_claim_status 和 ai_review_result 表状态"""

import os
import sys
import pymysql

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

print("=== ai_claim_status ===")
with conn.cursor() as cur:
    cur.execute("SELECT forceid, current_status, download_status, review_status, next_check_time, error_message FROM ai_claim_status ORDER BY created_at")
    rows = cur.fetchall()
    for row in rows:
        fid = row["forceid"]
        cs = row["current_status"]
        ds = row["download_status"]
        rs = row["review_status"]
        nc = row["next_check_time"]
        err = str(row.get("error_message") or "")[:50]
        print("  %s: current=%s, download=%s, review=%s, next_check=%s, err=%s" % (fid, cs, ds, rs, nc, err))

print("\n共 %d 条记录" % len(rows))

print("\n=== ai_review_result (manual_status) ===")
with conn.cursor() as cur:
    cur.execute("SELECT forceid, audit_result, manual_status, benefit_name FROM ai_review_result ORDER BY created_at")
    rows = cur.fetchall()
    for row in rows:
        fid = row["forceid"]
        ai = row["audit_result"]
        ms = row["manual_status"]
        bn = row["benefit_name"]
        print("  %s: ai=%s, manual=%s, benefit=%s" % (fid, ai, ms, bn))

print("\n共 %d 条记录" % len(rows))
conn.close()
