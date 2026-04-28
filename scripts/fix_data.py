#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据修复脚本：修复 ai_review_result 表中的三个已知数据问题

问题1: benefit_name 为空的案件填充为 "航班延误"
问题2: passenger_name 替换为 Applicant_Name（申请人）
问题3: 重复案件缺失基本信息，从本地 claim_info.json 回填

用法:
  python scripts/fix_data.py --dry-run          # 预览不执行
  python scripts/fix_data.py --fix-benefit       # 只修复问题1
  python scripts/fix_data.py --fix-passenger     # 只修复问题2
  python scripts/fix_data.py --fix-duplicate     # 只修复问题3
  python scripts/fix_data.py --all               # 全部修复
"""

import sys
import os
import json
import argparse
import pymysql
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.config import config


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


def find_claim_info_by_forceid(forceid: str) -> dict | None:
    """根据 forceid 查找 claim_info.json"""
    for info_file in config.CLAIMS_DATA_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                return data
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────
# 问题1: benefit_name 为空 → "航班延误"
# ─────────────────────────────────────────────

def fix_benefit_name(dry_run=False):
    """将 benefit_name 为空的记录填充为 '航班延误'"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM ai_review_result "
                "WHERE benefit_name IS NULL OR benefit_name = ''"
            )
            empty_count = cur.fetchone()["cnt"]
            print(f"[问题1] benefit_name 为空的记录数: {empty_count}")

            if empty_count == 0:
                print("  无需修复")
                return 0

            if dry_run:
                cur.execute(
                    "SELECT forceid, claim_id, benefit_name, created_at "
                    "FROM ai_review_result "
                    "WHERE benefit_name IS NULL OR benefit_name = '' "
                    "ORDER BY created_at ASC LIMIT 10"
                )
                print("  样本（前10条）:")
                for row in cur.fetchall():
                    print(f"    forceid={row['forceid']}, claim_id={row['claim_id']}, created={row['created_at']}")
                print(f"  [DRY RUN] 将执行: UPDATE ai_review_result SET benefit_name='航班延误' WHERE benefit_name IS NULL OR benefit_name=''")
                return empty_count

            cur.execute(
                "UPDATE ai_review_result SET benefit_name = '航班延误', updated_at = CURRENT_TIMESTAMP "
                "WHERE benefit_name IS NULL OR benefit_name = ''"
            )
            conn.commit()
            print(f"  已更新 {cur.rowcount} 条记录")
            return cur.rowcount
    finally:
        conn.close()


# ─────────────────────────────────────────────
# 问题2: passenger_name 替换为 Applicant_Name
# ─────────────────────────────────────────────

def fix_passenger_name(dry_run=False, limit=None):
    """
    将 passenger_name 从被保险人替换为申请人（Applicant_Name）。
    从 claim_info.json 中读取 Applicant_Name 更新到数据库。
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            query = "SELECT forceid, passenger_name FROM ai_review_result WHERE forceid IS NOT NULL AND forceid != ''"
            if limit:
                query += f" LIMIT {limit}"
            cur.execute(query)
            rows = cur.fetchall()

        print(f"[问题2] 待处理的记录数: {len(rows)}")

        updated = 0
        skipped = 0
        not_found = 0

        for i, row in enumerate(rows):
            forceid = row["forceid"]
            claim_info = find_claim_info_by_forceid(forceid)
            if not claim_info:
                not_found += 1
                continue

            applicant_name = claim_info.get("Applicant_Name", "")
            if not applicant_name:
                skipped += 1
                continue

            old_name = row.get("passenger_name", "")
            if old_name == applicant_name:
                skipped += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] forceid={forceid}: passenger_name '{old_name}' -> '{applicant_name}'")
                updated += 1
                continue

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE ai_review_result SET passenger_name = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE forceid = %s",
                    (applicant_name, forceid)
                )
            conn.commit()
            updated += 1

            if (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(rows)}")

        print(f"  已更新: {updated}, 跳过（无Applicant_Name或值相同）: {skipped}, 未找到claim_info: {not_found}")
        return updated
    finally:
        conn.close()


# ─────────────────────────────────────────────
# 问题3: 重复案件缺失基本信息回填
# ─────────────────────────────────────────────

def fix_duplicate_claims(dry_run=False):
    """
    找出被AI判断为重复案件但缺少基本信息的记录，从本地 claim_info.json 回填。
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT forceid, remark, claim_id, benefit_name,
                          insured_name, passenger_id_type, passenger_id_number
                   FROM ai_review_result
                   WHERE (remark LIKE '%重复%')
                   AND (claim_id IS NULL OR claim_id = ''
                        OR benefit_name IS NULL OR benefit_name = ''
                        OR insured_name IS NULL OR insured_name = '')
                   ORDER BY created_at ASC"""
            )
            rows = cur.fetchall()

        print(f"[问题3] 疑似重复案件（缺少基本信息）: {len(rows)}")

        if len(rows) == 0:
            print("  无需修复")
            return 0

        updates = []
        skipped_no_file = 0

        for row in rows:
            forceid = row["forceid"]
            claim_info = find_claim_info_by_forceid(forceid)
            if not claim_info:
                skipped_no_file += 1
                continue

            claim_id = claim_info.get("ClaimId", "")
            benefit_name = claim_info.get("BenefitName", "")
            insured_name = (
                claim_info.get("Insured_And_Policy") or claim_info.get("Insured_Name")
                or claim_info.get("Applicant_Name", "")
            )
            id_type = claim_info.get("ID_Type", "")
            id_number = claim_info.get("ID_Number", "")
            applicant_name = claim_info.get("Applicant_Name", "")
            passenger_name = applicant_name or insured_name

            updates.append((forceid, claim_id, benefit_name, insured_name,
                          passenger_name, id_type, id_number))

        print(f"  找到本地 claim_info.json 的: {len(updates)}")
        print(f"  未找到本地文件的: {skipped_no_file}")

        if dry_run:
            print("  预览更新:")
            for item in updates[:10]:
                fid, cid, bn, ins, pn, idt, idn = item
                print(f"    forceid={fid}:")
                print(f"      claim_id -> {cid}")
                print(f"      benefit_name -> {bn}")
                print(f"      insured_name -> {ins}")
                print(f"      passenger_name -> {pn}")
                print(f"      passenger_id_type -> {idt}")
                print(f"      passenger_id_number -> {idn}")
            if len(updates) > 10:
                print(f"    ... 还有 {len(updates) - 10} 条")
            print(f"  [DRY RUN] 将回填以上 {len(updates)} 条记录")
            return len(updates)

        updated = 0
        for forceid, claim_id, benefit_name, insured_name, passenger_name, id_type, id_number in updates:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE ai_review_result
                       SET claim_id = %s, benefit_name = %s, insured_name = %s,
                           passenger_name = %s, passenger_id_type = %s, passenger_id_number = %s,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE forceid = %s""",
                    (claim_id, benefit_name, insured_name, passenger_name, id_type, id_number, forceid)
                )
            conn.commit()
            updated += 1

        print(f"  已回填: {updated}")
        return updated
    finally:
        conn.close()


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="数据修复脚本")
    parser.add_argument("--dry-run", action="store_true", help="预览不执行")
    parser.add_argument("--fix-benefit", action="store_true", help="修复 benefit_name 为空")
    parser.add_argument("--fix-passenger", action="store_true", help="替换 passenger_name 为 Applicant_Name")
    parser.add_argument("--fix-duplicate", action="store_true", help="回填重复案件缺失信息")
    parser.add_argument("--all", action="store_true", help="全部修复")
    parser.add_argument("--limit", type=int, help="限制处理数量（仅问题2）")
    args = parser.parse_args()

    if not (args.fix_benefit or args.fix_passenger or args.fix_duplicate or args.all):
        parser.print_help()
        sys.exit(1)

    dry_run = args.dry_run

    if args.fix_benefit or args.all:
        fix_benefit_name(dry_run)
        print()

    if args.fix_passenger or args.all:
        fix_passenger_name(dry_run, args.limit)
        print()

    if args.fix_duplicate or args.all:
        fix_duplicate_claims(dry_run)
        print()

    if dry_run:
        print("\n=== DRY RUN 模式，未实际修改数据库 ===")
        print("确认无误后，去掉 --dry-run 参数执行")


if __name__ == "__main__":
    main()
