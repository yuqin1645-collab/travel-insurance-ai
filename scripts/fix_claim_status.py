#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用正确的 ClaimId 重建 17 个案件的 claim_status 记录"""
import sys, os, json, pymysql
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env')

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.state.constants import ClaimStatus
from app.config import config

CLAIMS_DIR = config.CLAIMS_DATA_DIR

# forceid -> 正确的 ClaimId 映射
# 从 ai_review_result 表中获取
all_cases = [
    ("202604001809", "a0nC800000LJUkfIAH"),
    ("202604001829", "a0nC800000LOKG2IAP"),
    ("202604001830", "a0nC800000LOWSUIA5"),
    ("202604001831", "a0nC800000LObjtIAD"),
    ("202604001835", "a0nC800000LP6z3IAD"),
    ("202604001836", "a0nC800000LPXO5IAP"),
    ("202604001837", "a0nC800000LPXO6IAP"),
    ("202604001841", "a0nC800000LQDaDIAX"),
    ("202604001854", "a0nC800000LRyvFIAT"),
    ("202604001855", "a0nC800000LRsBWIA1"),
    ("202604001861", "a0nC800000LT6VqIAL"),
    ("202604001863", "a0nC800000LTx5TIAT"),
    ("202604001956", "a0nC800000LjYSsIAN"),
    ("202604001959", "a0nC800000LjwLWIAZ"),
    ("202604001960", "a0nC800000Lk4EDIAZ"),
    ("202604002169", "a0nC800000LoH6RIAV"),
    ("202604002170", "a0nC800000Loem9IAB"),
]

conn = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME', 'ai'), charset='utf8mb4',
)
cur = conn.cursor()

done = 0
for claim_id, forceid in all_cases:
    # 找 claim_type
    claim_type = "flight_delay"
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            d = json.loads(info_file.read_text(encoding="utf-8"))
            if str(d.get("forceid") or "") == forceid:
                benefit = str(d.get("BenefitName") or "")
                claim_type = "flight_delay" if "延误" in benefit else "baggage_damage"
                break
        except Exception:
            continue

    try:
        # 用 INSERT ... ON DUPLICATE KEY UPDATE forceid=forceid 来强制插入（如果forceid已存在则更新）
        # 先查是否已存在
        cur.execute("SELECT id FROM ai_claim_status WHERE forceid=%s", (forceid,))
        existing = cur.fetchone()

        if existing:
            # 更新现有记录的 claim_id
            cur.execute("""
                UPDATE ai_claim_status
                SET claim_id=%s, claim_type=%s, updated_at=NOW()
                WHERE forceid=%s
            """, (claim_id, claim_type, forceid))
            conn.commit()
            print("  [更新] %s (%s) -> claim_id=%s" % (forceid, claim_type, claim_id))
        else:
            # 插入新记录（需要先删掉可能存在的重复forceid）
            # 不可能走到这里，因为所有forceid都已存在
            cur.execute("""
                INSERT INTO ai_claim_status
                (claim_id, forceid, claim_type, current_status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, NOW(), NOW())
            """, (claim_id, forceid, claim_type, ClaimStatus.DOWNLOADED))
            conn.commit()
            print("  [插入] %s -> %s" % (forceid, claim_id))

        done += 1
    except Exception as e:
        print("  [FAIL] %s: %s" % (claim_id, e))

cur.close()
conn.close()
print("\n完成: %d/%d" % (done, len(all_cases)))

# 验证
print("\n验证:")
conn2 = pymysql.connect(
    host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME', 'ai'), charset='utf8mb4',
)
cur2 = conn2.cursor()
ph = ','.join(['%s'] * len(all_cases))
cur2.execute("SELECT claim_id, forceid, current_status FROM ai_claim_status WHERE forceid IN (" + ph + ")",
             [fid for _, fid in all_cases])
rows = {r[1]: r for r in cur2.fetchall()}
cur2.close()
conn2.close()

for claim_id, forceid in all_cases:
    r = rows.get(forceid)
    if r:
        print("  [✓] %s | %s | %s" % (r[0], forceid, r[2]))
    else:
        print("  [✗] %s | %s | MISSING" % (claim_id, forceid))
