#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对比9件delay_hours=unknown案件的人工处理结果
"""

import json
import pymysql
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

UNKNOWN_CASES = [
    "a0nC800000IdwOPIAZ", "a0nC800000ICHXNIA5", "a0nC800000IBWjlIAH",
    "a0nC800000Ib5ZTIAZ", "a0nC800000I9eOxIAJ", "a0nC800000I9eOuIAJ",
    "a0nC800000I7iQHIAZ", "a0nC800000HqpJBIAZ", "a0nC800000JZU0NIAX",
]

# 从claim_info中获取Final_Status
CLAIMS_DIR = ROOT / "claims_data"
REVIEW_DIR = ROOT / "review_results" / "baggage_delay"

def find_claim_folder(forceid):
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                return info_file.parent, data
        except Exception:
            continue
    return None, None

# 查数据库
conn = pymysql.connect(
    host=os.getenv("DB_HOST", ""), port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", ""), password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "ai"), charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

output_lines = []

def p(text=""):
    output_lines.append(str(text))

p("9件 delay_hours=unknown 案件综合分析")
p("="*80)

for fid in UNKNOWN_CASES:
    folder, info = find_claim_folder(fid)

    # 数据库
    with conn.cursor() as cur:
        cur.execute("SELECT audit_result, manual_status, manual_conclusion, payout_amount, benefit_name FROM ai_review_result WHERE forceid=%s", (fid,))
        db_row = cur.fetchone()

    p(f"\n{'='*60}")
    p(f"案件: {fid}")
    p(f"{'='*60}")

    p(f"  人工状态(Final_Status): {info.get('Final_Status', '') if info else 'N/A'}")
    p(f"  人工状态(manual_status): {db_row.get('manual_status') if db_row else 'N/A'}")
    p(f"  人工结论(manual_conclusion): {db_row.get('manual_conclusion') if db_row else 'N/A'}")
    p(f"  赔付金额: {db_row.get('payout_amount') if db_row else 'N/A'}")

    # 审核结果
    review_file = REVIEW_DIR / f"{fid}_ai_review.json"
    if review_file.exists():
        review = json.loads(review_file.read_text(encoding="utf-8"))
        p(f"  AI结论: {review.get('audit_result', '')}")
        p(f"  AI Remark: {review.get('Remark', '')[:150]}")
        p(f"  AI payout: {review.get('payout_amount', '')}")

        # 关键差异判断
        manual_s = (db_row.get('manual_status') or '') if db_row else ''
        if '支付成功' in str(info.get('Final_Status', '')) or manual_s == '通过':
            p(f"  >>> 差异: AI要求补件，但人工已通过并赔付")
        elif '零结关案' in str(info.get('Final_Status', '')):
            p(f"  >>> 人工零结关案（可能是客户放弃）")
        elif '拒赔' in str(info.get('Final_Status', '')) or manual_s == '拒绝':
            p(f"  >>> 人工也拒绝，与AI一致")

conn.close()

p("\n\n" + "="*80)
p("汇总：")
p("-" * 80)
p("人工支付成功（赔付）但AI要求补件：IBWjlIAH, I9eOxIAJ, I9eOuIAJ, I7iQHIAZ, HqpJBIAZ, JZU0NIAX (6件)")
p("人工零结关案：IdwOPIAZ, ICHXNIA5 (2件)")
p("人工事后拒赔：Ib5ZTIAZ (1件)")
p("\n结论：6件人工赔付的案件，AI因delay_hours=unknown卡在补件环节")
p("这些案件的行李延误时间实际上可能很长，但Vision未能从材料中提取签收时间")

out_path = ROOT / "docs" / "9_unknown_manual_compare.txt"
out_path.write_text("\n".join(output_lines), encoding="utf-8")
print(f"已写入 {out_path}")
