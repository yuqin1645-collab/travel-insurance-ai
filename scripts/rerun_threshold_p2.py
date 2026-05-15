#!/usr/bin/env python3
"""
批量重跑 P2 未达门槛案件并统计修复效果。
用法: python scripts/rerun_threshold_p2.py [--limit N]
"""

import sys
import os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from dotenv import load_dotenv
load_dotenv()

import pymysql
import asyncio
import aiohttp
import json
import re
import time
from pathlib import Path
from collections import Counter

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


def load_threshold_forceids(limit=None):
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT forceid, remark, manual_conclusion, payout_amount
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
                m = re.search(r'延误时长(\d+)分钟', remark)
                old_delay = int(m.group(1)) if m else 0
                forceids.append({
                    'forceid': r['forceid'],
                    'old_delay': old_delay,
                    'old_remark': remark[:100],
                    'manual_conclusion': r.get('manual_conclusion', ''),
                })

        return forceids[:limit] if limit else forceids
    finally:
        conn.close()


def find_claim_path(forceid: str) -> Path | None:
    """查找案件目录。"""
    claim_base = Path("claims_data")
    if not claim_base.exists():
        return None

    for d in claim_base.iterdir():
        if not d.is_dir():
            continue
        try:
            for sub in d.iterdir():
                if sub.is_dir() and forceid in sub.name:
                    return sub
        except PermissionError:
            continue
    return None


async def rerun_single(forceid: str) -> dict:
    """重跑单个案件。"""
    claim_path = find_claim_path(forceid)
    if not claim_path:
        return {"forceid": forceid, "status": "error", "reason": "directory not found"}

    claim_info_path = claim_path / "claim_info.json"
    if not claim_info_path.exists():
        return {"forceid": forceid, "status": "error", "reason": "claim_info.json missing"}

    with open(claim_info_path, encoding='utf-8') as f:
        claim_info = json.load(f)

    reviewer = AIClaimReviewer()
    policy_terms = ""

    async with aiohttp.ClientSession() as session:
        result = await review_flight_delay_async(
            reviewer=reviewer,
            claim_folder=claim_path,
            claim_info=claim_info,
            policy_terms=policy_terms,
            index=1,
            total=1,
            session=session,
        )

    audit = result.get('flight_delay_audit', {})
    audit_result = audit.get('audit_result', '')
    key_data = audit.get('key_data', {})
    delay_min = key_data.get('delay_duration_minutes')
    explanation = audit.get('explanation', '')

    return {
        'forceid': forceid,
        'audit_result': audit_result,
        'delay_minutes': delay_min,
        'explanation': explanation[:100],
        'status': 'done',
    }


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--indices', type=str, default=None, help='Comma-separated indices to run')
    args = parser.parse_args()

    forceids = load_threshold_forceids(limit=args.limit)
    print(f'找到 {len(forceids)} 件未达门槛 P2 案件\n')

    # Filter by indices if specified
    if args.indices:
        indices = [int(x) for x in args.indices.split(',')]
        forceids = [forceids[i] for i in indices if i < len(forceids)]
        print(f'按索引筛选后: {len(forceids)} 件\n')

    if not forceids:
        print('无案件需要重跑。')
        return

    results = []
    passed = 0
    rejected = 0
    errors = 0

    for i, item in enumerate(forceids):
        fid = item['forceid']
        print(f'[{i+1}/{len(forceids)}] 重跑 {fid} (原延误={item["old_delay"]}min)...')

        try:
            result = await rerun_single(fid)
        except Exception as e:
            print(f'  异常: {e}')
            results.append({'forceid': fid, 'status': 'error', 'reason': str(e)[:80]})
            errors += 1
            continue

        if result['status'] == 'error':
            print(f'  错误: {result.get("reason", "unknown")}')
            errors += 1
            continue

        new_delay = result.get('delay_minutes', '?')
        audit = result['audit_result']
        print(f'  结果: AI={audit}, 延误={new_delay}min')

        if audit == '通过':
            passed += 1
            print(f'  ✅ 修复成功')
        elif audit == '拒绝':
            rejected += 1
            if new_delay and isinstance(new_delay, int) and new_delay >= 300:
                print(f'  ⚠️  延误达标但仍拒绝')
            else:
                print(f'  ❌ 仍未达标')
        elif audit == '需补齐资料':
            print(f'  ⏸️  需补齐资料')

        results.append(result)

        # Rate limiting to avoid API overload
        if i < len(forceids) - 1:
            await asyncio.sleep(2)

    # Summary
    print(f'\n{"="*60}')
    print(f'批量重跑完成: {len(results)} 件')
    print(f'  通过: {passed}')
    print(f'  拒绝: {rejected}')
    print(f'  错误: {errors}')
    print(f'  修复率: {passed}/{len(results)} = {passed*100/len(results):.1f}%' if results else 'N/A')

    # Save detailed results
    with open('docs/p2_rerun_results.json', 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f'\n详细结果已保存到 docs/p2_rerun_results.json')


if __name__ == '__main__':
    asyncio.run(main())
