#!/usr/bin/env python3
"""
批量重跑 P2 未达门槛案件，统计修复效果。
用法: python scripts/rerun_threshold_p2.py
"""

import sys
import os
import json
import re
from collections import Counter

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

import pymysql
import asyncio
import aiohttp
from pathlib import Path

from scripts.find_claim_by_forceid import fetch_by_forceid
from app.modules.flight_delay.pipeline import review_flight_delay_async
from app.claim_ai_reviewer import AIClaimReviewer


def get_db_conn():
    return pymysql.connect(
        host=os.getenv('DB_HOST', ''),
        user=os.getenv('DB_USER', ''),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', ''),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


def load_threshold_forceids():
    """从 DB 加载当前未达门槛 P2 案件。"""
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT forceid, remark, audit_result, manual_status, manual_conclusion
                FROM ai_review_result
                WHERE claim_type = 'flight_delay'
                AND audit_result = '拒绝'
                AND manual_status = '通过'
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

        forceids = []
        for r in rows:
            remark = r.get('remark', '')
            if any(kw in remark for kw in ['未达门槛', '延误0分钟', '延误时长']):
                forceids.append(r['forceid'])

        return forceids
    finally:
        conn.close()


async def rerun_single(forceid: str) -> dict:
    """重跑单个案件，返回摘要。"""
    claim_info = fetch_by_forceid(forceid)
    if not claim_info or isinstance(claim_info, str):
        return {"forceid": forceid, "status": "error", "reason": "claim not found"}

    # 查找案件目录
    from scripts.find_claim_by_forceid import fetch_by_forceid as fetch
    # fetch_by_forceid returns claim_info dict, we need the path
    # Try to find the directory
    claim_base_dir = Path("claims_data")
    found_path = None

    for d in claim_base_dir.iterdir():
        if d.is_dir() and d.name not in (".download_progress.json",):
            for sub in d.iterdir():
                if sub.is_dir() and forceid in sub.name:
                    found_path = sub
                    break
        if found_path:
            break

    if not found_path:
        return {"forceid": forceid, "status": "error", "reason": "directory not found"}

    claim_info_path = found_path / "claim_info.json"
    if not claim_info_path.exists():
        return {"forceid": forceid, "status": "error", "reason": "claim_info.json missing"}

    with open(claim_info_path, encoding='utf-8') as f:
        claim_info = json.load(f)

    reviewer = AIClaimReviewer()
    policy_terms = ""

    async with aiohttp.ClientSession() as session:
        result = await review_flight_delay_async(
            reviewer=reviewer,
            claim_folder=found_path,
            claim_info=claim_info,
            policy_terms=policy_terms,
            index=1,
            total=1,
            session=session,
        )

    remark = result.get("Remark", "")
    audit = result.get("flight_delay_audit", {})
    audit_result = audit.get("audit_result", "")

    # Extract delay minutes from Remark
    delay_m = re.search(r'延误时长?(\d+)分钟', remark)
    delay_min = int(delay_m.group(1)) if delay_m else None

    return {
        "forceid": forceid,
        "audit_result": audit_result,
        "delay_minutes": delay_min,
        "remark": remark[:150],
        "status": "done",
    }


def main():
    print("=== P2 未达门槛案件批量重跑 ===\n")

    # Step 1: Load current P2 threshold cases from DB
    print("Step 1: 从数据库加载未达门槛案件...")
    forceids = load_threshold_forceids()
    print(f"  找到 {len(forceids)} 件\n")

    if not forceids:
        print("无案件需要重跑。")
        return

    # Step 2: Save list for review.py batch processing
    output_file = "docs/p2_threshold_forceids.json"
    with open(output_file, 'w') as f:
        json.dump(forceids, f, indent=2, ensure_ascii=False)
    print(f"Step 2: 已保存 forceid 列表到 {output_file}")
    print(f"  前5个: {forceids[:5]}")
    print(f"\nStep 3: 使用以下命令批量重跑:")
    print(f"  python scripts/review.py --forceid {' --forceid '.join(forceids[:5])} ...")
    print(f"\n或使用循环脚本逐个重跑。")


if __name__ == "__main__":
    main()
