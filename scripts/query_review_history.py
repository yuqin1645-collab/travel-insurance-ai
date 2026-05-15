#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审核历史版本查询工具

用法:
  python query_review_history.py --forceid xxx              # 查看某案件完整历史
  python query_review_history.py --comparison               # 全量 AI vs 人工对比
  python query_review_history.py --comparison --limit 50    # 对比 Top 50
  python query_review_history.py --stats                    # 统计概览
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

try:
    import pymysql
except ImportError as e:
    print(f"缺少依赖: {e}")
    sys.exit(1)


TABLE_HISTORY = "ai_review_history"
TABLE_REVIEW_RESULT = "ai_review_result"


def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", ""),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


# ─────────────────────────────────────────────
# 命令 1: 查询单个 forceid 的历史版本
# ─────────────────────────────────────────────

def cmd_forceid(forceid: str):
    print(f"\n{'='*80}")
    print(f"案件历史版本: {forceid}")
    print(f"{'='*80}\n")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {TABLE_HISTORY} WHERE forceid=%s ORDER BY created_at ASC",
                (forceid,)
            )
            rows = cur.fetchall()

        if not rows:
            print(f"  无历史记录: {forceid}")
            return

        # 查主表当前值
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT audit_result, manual_status, payout_amount, "
                f"first_ai_audit_result, first_ai_manual_status, first_ai_audit_time "
                f"FROM {TABLE_REVIEW_RESULT} WHERE forceid=%s",
                (forceid,)
            )
            current = cur.fetchone()

        print(f"{'版本':>3} {'类型':>7} {'AI结果':>8} {'AI置信度':>8} {'人工状态':>10} {'赔付金额':>8} {'时间'}")
        print("-" * 90)

        for i, row in enumerate(rows, 1):
            v_type = row.get('review_type', '')
            ai_result = row.get('audit_result', '-') or '-'
            confidence = row.get('confidence_score', '-')
            manual_st = row.get('manual_status', '-') or '-'
            payout = row.get('payout_amount', '-')
            created = row.get('created_at', '-')
            print(f"{i:>3} {v_type:>7} {ai_result:>8} {str(confidence):>8} {manual_st:>10} {str(payout):>8} {created}")

        if current:
            print(f"\n--- 当前状态 ---")
            print(f"  AI结果: {current.get('audit_result', 'N/A')}")
            print(f"  人工状态: {current.get('manual_status', 'N/A')}")
            print(f"  首次AI结果: {current.get('first_ai_audit_result', 'N/A')}")
            print(f"  首次AI时间: {current.get('first_ai_audit_time', 'N/A')}")
            print(f"  首次时人工状态: {current.get('first_ai_manual_status', 'N/A')}")

        print(f"\n共 {len(rows)} 条历史记录")

        # 显示差异
        ai_rows = [r for r in rows if r.get('review_type') == 'ai']
        manual_rows = [r for r in rows if r.get('review_type') == 'manual']

        if len(ai_rows) > 1:
            print(f"\nAI 结论变更 {len(ai_rows)} 次")
            for i in range(1, len(ai_rows)):
                prev = ai_rows[i-1].get('audit_result')
                curr = ai_rows[i].get('audit_result')
                if prev != curr:
                    print(f"  版本{i} → 版本{i+1}: {prev} → {curr}")

    finally:
        conn.close()


# ─────────────────────────────────────────────
# 命令 2: 全量 AI vs 人工对比
# ─────────────────────────────────────────────

def cmd_comparison(limit: int = 100, benefit: str = None):
    print(f"\n{'='*80}")
    print(f"AI vs 人工全量对比 (最近 {limit} 条 AI 历史版本)")
    print(f"{'='*80}\n")

    conn = get_conn()
    try:
        where = "WHERE h.review_type = 'ai'"
        params: list = []
        if benefit:
            where += " AND h.benefit_name = %s"
            params.append(benefit)

        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT
                    h.forceid, h.benefit_name,
                    h.audit_result AS ai_result,
                    h.confidence_score AS ai_confidence,
                    h.payout_amount AS ai_payout,
                    h.manual_status AS snapshot_manual,
                    r.audit_result AS current_ai,
                    r.manual_status AS current_manual,
                    r.first_ai_audit_result AS first_ai,
                    h.created_at
                FROM {TABLE_HISTORY} h
                LEFT JOIN {TABLE_REVIEW_RESULT} r ON h.forceid = r.forceid
                {where}
                ORDER BY h.created_at DESC LIMIT %s""",
                params + [limit]
            )
            rows = cur.fetchall()

        if not rows:
            print("  无数据（请先运行审核或同步人工状态）")
            return

        # 统计
        consistent = 0
        ai_changed = 0
        ai_only = 0

        print(f"{'案件ID':>12} {'险种':>8} {'AI结果':>8} {'当前AI':>8} {'当前人工':>10} {'对比':>10} {'时间'}")
        print("-" * 100)

        for row in rows:
            forceid = str(row.get('forceid', ''))[:12]
            benefit = row.get('benefit_name', '')[:8]
            ai_r = row.get('ai_result', '-') or '-'
            curr_ai = row.get('current_ai', '-') or '-'
            curr_manual = row.get('current_manual', '-') or '-'
            created = row.get('created_at', '')

            if not curr_manual or curr_manual == 'None':
                flag = '仅AI'
                ai_only += 1
            elif ai_r == curr_ai and curr_ai == curr_manual:
                flag = '一致'
                consistent += 1
            elif ai_r != curr_ai:
                flag = 'AI变更'
                ai_changed += 1
            elif ai_r != curr_manual:
                flag = 'AI≠人工'
                ai_changed += 1
            else:
                flag = '其他'
                ai_changed += 1

            print(f"{forceid:>12} {benefit:>8} {ai_r:>8} {curr_ai:>8} {curr_manual:>10} {flag:>10} {created}")

        print(f"\n--- 对比统计 ---")
        print(f"  一致: {consistent}")
        print(f"  AI结论变更/差异: {ai_changed}")
        print(f"  仅AI审核(无人工): {ai_only}")
        print(f"  总计: {len(rows)}")

    finally:
        conn.close()


# ─────────────────────────────────────────────
# 命令 3: 统计概览
# ─────────────────────────────────────────────

def cmd_stats():
    print(f"\n{'='*80}")
    print("审核历史统计概览")
    print(f"{'='*80}\n")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 总案件数
            cur.execute(f"SELECT COUNT(*) as cnt FROM {TABLE_REVIEW_RESULT}")
            total_cases = cur.fetchone()['cnt']

            # 有历史的案件数
            cur.execute(f"SELECT COUNT(DISTINCT forceid) as cnt FROM {TABLE_HISTORY}")
            cases_with_history = cur.fetchone()['cnt']

            # AI 版本总数
            cur.execute(f"SELECT COUNT(*) as cnt FROM {TABLE_HISTORY} WHERE review_type='ai'")
            total_ai_versions = cur.fetchone()['cnt']

            # 人工版本总数
            cur.execute(f"SELECT COUNT(*) as cnt FROM {TABLE_HISTORY} WHERE review_type='manual'")
            total_manual_versions = cur.fetchone()['cnt']

            # AI 结论变更案件数（有多条 AI 历史且结论不同的）
            cur.execute(
                f"""SELECT COUNT(*) as cnt FROM (
                    SELECT forceid, COUNT(DISTINCT audit_result) as diff_count
                    FROM {TABLE_HISTORY}
                    WHERE review_type='ai'
                    GROUP BY forceid
                    HAVING diff_count > 1
                ) t"""
            )
            changed_cases = cur.fetchone()['cnt']

            # 有里程碑的案件数
            cur.execute(
                f"SELECT COUNT(*) as cnt FROM {TABLE_REVIEW_RESULT} WHERE first_ai_audit_time IS NOT NULL"
            )
            milestone_cases = cur.fetchone()['cnt']

        print(f"  数据库总案件数: {total_cases}")
        print(f"  有历史记录的案件: {cases_with_history} ({cases_with_history/max(total_cases,1)*100:.1f}%)")
        print(f"  AI 历史版本总数: {total_ai_versions}")
        print(f"  人工历史版本总数: {total_manual_versions}")
        print(f"  AI 结论发生过变更的案件: {changed_cases}")
        print(f"  已设置首次AI里程碑的案件: {milestone_cases}")

        # 按险种分布
        print(f"\n--- 按险种分布 ---")
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT benefit_name, COUNT(DISTINCT forceid) as cases,
                           COUNT(*) as versions
                    FROM {TABLE_HISTORY}
                    GROUP BY benefit_name ORDER BY cases DESC"""
            )
            for row in cur.fetchall():
                print(f"  {row['benefit_name'] or '未知':>10}: {row['cases']} 案件, {row['versions']} 版本")

    finally:
        conn.close()


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="审核历史版本查询工具")
    parser.add_argument("--forceid", type=str, help="查询指定 forceid 的历史版本")
    parser.add_argument("--comparison", action="store_true", help="全量 AI vs 人工对比")
    parser.add_argument("--limit", type=int, default=100, help="对比查询限制条数 (默认 100)")
    parser.add_argument("--type", type=str, help="按险种过滤 (baggage/flight/随身财产)")
    parser.add_argument("--stats", action="store_true", help="统计概览")

    args = parser.parse_args()

    if args.forceid:
        cmd_forceid(args.forceid)
    elif args.comparison:
        cmd_comparison(limit=args.limit, benefit=args.type)
    elif args.stats:
        cmd_stats()
    else:
        parser.print_help()
        print("\n推荐操作:")
        print("  --stats          先看统计概览")
        print("  --forceid xxx    查看具体案件历史")
        print("  --comparison     全量 AI vs 人工对比")


if __name__ == "__main__":
    main()
