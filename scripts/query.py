#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一查询脚本：查询案件/数据库状态/审核分析

用法:
  python query.py forceid xxx              # 查询 forceid 对应案件路径
  python query.py status                   # 查看数据库表状态概览
  python query.py count                    # 统计各险种审核结果数量
"""

import sys
import json
import os
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.config import config

REVIEW_DIR = config.REVIEW_RESULTS_DIR
CLAIMS_DIR = config.CLAIMS_DATA_DIR


def cmd_forceid(forceid: str):
    """查询 forceid 对应的案件路径和审核结果"""
    # 找 claim_info
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                print(f"案件目录: {info_file.parent}")
                print(f"  CaseNo: {data.get('CaseNo', '')}")
                print(f"  BenefitName: {data.get('BenefitName', '')}")
                print(f"  Final_Status: {data.get('Final_Status', '')}")
                break
        except Exception:
            continue
    else:
        print(f"claims_data 中未找到: {forceid}")

    # 找审核结果
    result_file = next(REVIEW_DIR.rglob(f"{forceid}_ai_review.json"), None)
    if result_file:
        data = json.loads(result_file.read_text(encoding="utf-8"))
        print(f"\n审核结果: {result_file}")
        print(f"  Remark: {data.get('Remark', '')}")
        print(f"  IsAdditional: {data.get('IsAdditional', '')}")
    else:
        print(f"\nreview_results 中未找到审核结果")


def cmd_status():
    """查看数据库状态概览"""
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
    try:
        with conn.cursor() as cur:
            # 总数
            cur.execute("SELECT COUNT(*) as cnt FROM ai_review_result")
            total = cur.fetchone()["cnt"]
            print(f"ai_review_result 总记录: {total}")

            # 按 audit_result 分布
            cur.execute("SELECT audit_result, COUNT(*) as cnt FROM ai_review_result GROUP BY audit_result")
            for row in cur.fetchall():
                print(f"  {row['audit_result']}: {row['cnt']}")

            # 最新5条
            cur.execute("SELECT forceid, remark, created_at FROM ai_review_result ORDER BY created_at DESC LIMIT 5")
            print(f"\n最新5条:")
            for row in cur.fetchall():
                print(f"  {row['forceid']}: {str(row['remark'])[:50]}... ({row['created_at']})")
    finally:
        conn.close()


def cmd_count():
    """统计各险种审核结果数量"""
    counts = {"flight_delay": 0, "baggage_delay": 0, "other": 0}
    for f in REVIEW_DIR.rglob("*_ai_review.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            ct = data.get("claim_type", "")
            if ct == "flight_delay":
                counts["flight_delay"] += 1
            elif ct == "baggage_delay":
                counts["baggage_delay"] += 1
            else:
                counts["other"] += 1
        except Exception:
            pass

    print("审核结果统计:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print(f"  总计: {sum(counts.values())}")


def main():
    parser = argparse.ArgumentParser(description="统一查询脚本")
    sub = parser.add_subparsers(dest="action")

    sub.add_parser("status", help="查看数据库状态概览")
    sub.add_parser("count", help="统计各险种审核结果数量")
    forceid_p = sub.add_parser("forceid", help="查询 forceid 对应案件")
    forceid_p.add_argument("query", help="forceid 值")

    args = parser.parse_args()

    if not args.action:
        parser.print_help()
        sys.exit(1)

    if args.action == "forceid":
        cmd_forceid(args.query)
    elif args.action == "status":
        cmd_status()
    elif args.action == "count":
        cmd_count()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
