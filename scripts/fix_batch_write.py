#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复8个新案件的数据库写入 + 补充9个已入库案件的 claim_status
"""

import sys, os, json, pymysql
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env')

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.production.main_workflow import ProductionWorkflow
from app.state.constants import ClaimStatus
from app.config import config

CLAIMS_DIR = config.CLAIMS_DATA_DIR


def get_db():
    return pymysql.connect(
        host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'ai'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


# 8个需要写入数据库的新案件
new_cases = [
    ("202604001854", "a0nC800000LRyvFIAT"),
    ("202604001861", "a0nC800000LT6VqIAL"),
    ("202604001863", "a0nC800000LTx5TIAT"),
    ("202604001956", "a0nC800000LjYSsIAN"),
    ("202604001959", "a0nC800000LjwLWIAZ"),
    ("202604001960", "a0nC800000Lk4EDIAZ"),
    ("202604002169", "a0nC800000LoH6RIAV"),
    ("202604002170", "a0nC800000Loem9IAB"),
]

# 9个已入库但缺 claim_status 的案件
existing_cases = [
    ("202604001809", "a0nC800000LJUkfIAH"),
    ("202604001829", "a0nC800000LOKG2IAP"),
    ("202604001830", "a0nC800000LOWSUIA5"),
    ("202604001831", "a0nC800000LObjtIAD"),
    ("202604001835", "a0nC800000LP6z3IAD"),
    ("202604001836", "a0nC800000LPXO5IAP"),
    ("202604001837", "a0nC800000LPXO6IAP"),
]

wf = ProductionWorkflow()
workflow_method = wf._extract_review_fields

print("=" * 70)
print("修复数据库写入")
print("=" * 70)

# ── 第1步：写入8个新案件的数据库 ──
print("\n[1/2] 写入8个新案件的 ai_review_result + ai_claim_status...")

for claim_id, forceid in new_cases:
    # 找本地审核结果
    result_file = None
    for f in config.REVIEW_RESULTS_DIR.rglob("*_ai_review.json"):
        if f.stem.replace('_ai_review', '') == forceid:
            result_file = f
            break

    if not result_file:
        print(f"  [!] 找不到审核结果文件: {claim_id} ({forceid})")
        continue

    try:
        result_data = json.loads(result_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [!] 读取审核结果失败: {claim_id}: {e}")
        continue

    # 找 claim_info
    claim_folder = None
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            d = json.loads(info_file.read_text(encoding="utf-8"))
            if str(d.get("forceid") or "") == forceid:
                claim_folder = info_file.parent
                break
        except Exception:
            continue

    if not claim_folder:
        print(f"  [!] 找不到 claim_info: {claim_id}")
        continue

    claim_info = json.loads((claim_folder / "claim_info.json").read_text(encoding="utf-8"))
    benefit = str(claim_info.get("BenefitName") or "")
    claim_type = "flight_delay" if "延误" in benefit else "baggage_damage"

    # 抽取字段
    try:
        fields = workflow_method(result_data, claim_info)
    except Exception as e:
        print(f"  [!] 抽取字段失败: {claim_id}: {e}")
        continue

    # 写入 ai_claim_status
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO ai_claim_status (claim_id, forceid, claim_type, current_status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE updated_at=NOW(), current_status=VALUES(current_status)
        ''', (claim_id, forceid, claim_type, ClaimStatus.DOWNLOADED))
        conn.commit()
        print(f"  [✓] claim_status: {claim_id}")
    except Exception as e:
        print(f"  [!] claim_status 写入失败: {claim_id}: {e}")

    # 写入 ai_review_result
    keys = list(fields.keys())
    placeholders = ', '.join(['%s'] * len(keys))
    update_clause = ', '.join([f"{k}=VALUES({k})" for k in keys if k != 'forceid'])
    sql = (f"INSERT INTO ai_review_result ({', '.join(keys)}) "
           f"VALUES ({placeholders}) "
           f"ON DUPLICATE KEY UPDATE {update_clause}, updated_at=CURRENT_TIMESTAMP")

    try:
        cur.execute(sql, list(fields.values()))
        conn.commit()
        print(f"  [✓] ai_review_result: {claim_id} | audit_result={fields.get('audit_result','')}")
    except Exception as e:
        print(f"  [!] ai_review_result 写入失败: {claim_id}: {e}")

    cur.close()
    conn.close()

# ── 第2步：补充9个已入库案件的 claim_status ──
print("\n[2/2] 补充9个已入库案件的 claim_status...")

for claim_id, forceid in existing_cases:
    # 找本地 claim_info
    claim_folder = None
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            d = json.loads(info_file.read_text(encoding="utf-8"))
            if str(d.get("forceid") or "") == forceid:
                claim_folder = info_file.parent
                benefit = str(d.get("BenefitName") or "")
                claim_type = "flight_delay" if "延误" in benefit else "baggage_damage"
                break
        except Exception:
            continue

    if not claim_folder:
        print(f"  [!] 找不到 claim_info: {claim_id}")
        continue

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute('''
            INSERT INTO ai_claim_status (claim_id, forceid, claim_type, current_status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE updated_at=NOW(), current_status=VALUES(current_status)
        ''', (claim_id, forceid, claim_type, ClaimStatus.DOWNLOADED))
        conn.commit()
        print(f"  [✓] {claim_id}")
    except Exception as e:
        print(f"  [!] 失败: {claim_id}: {e}")
    cur.close()
    conn.close()

# ── 最终验证 ──
print("\n" + "=" * 70)
print("最终验证")
print("=" * 70)

all_ids = [cid for cid, _ in new_cases + existing_cases]

conn = get_db()
cur = conn.cursor()

# 检查 ai_review_result
cur.execute(
    "SELECT claim_id, forceid, audit_result, manual_status FROM ai_review_result WHERE claim_id IN (%s)" % ','.join(['%s']*len(all_ids)),
    all_ids
)
rr_rows = {r['claim_id']: r for r in cur.fetchall()}

# 检查 ai_claim_status
cur.execute(
    "SELECT claim_id, forceid, current_status FROM ai_claim_status WHERE claim_id IN (%s)" % ','.join(['%s']*len(all_ids)),
    all_ids
)
cs_rows = {r['claim_id']: r for r in cur.fetchall()}

cur.close()
conn.close()

all_ok = True
for cid, fid in new_cases + existing_cases:
    rr = rr_rows.get(cid)
    cs = cs_rows.get(cid)
    if not rr:
        print(f"  [!] {cid} 缺失 ai_review_result")
        all_ok = False
    elif not cs:
        print(f"  [!] {cid} 缺失 ai_claim_status")
        all_ok = False
    else:
        print(f"  [✓] {cid} | {fid} | AI:{rr.get('audit_result','')} | 人工:{rr.get('manual_status','')} | claim_status:✓")

print()
if all_ok:
    print("全部 %d 个案件完成！" % len(all_ids))
else:
    print("部分案件仍有问题，请检查。")
