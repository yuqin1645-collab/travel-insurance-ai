#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航班延误专项分析：从数据库提取P0/P1/P2分类统计

用法:
  python scripts/analyze_flight_delay.py          # 全量分析
  python scripts/analyze_flight_delay.py --sample  # 显示样例
"""

import os
import sys
import json
import random
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

import pymysql

def connect_db():
    return pymysql.connect(
        host=os.getenv("DB_HOST", ""),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def normalize_audit(result):
    """标准化AI审核结果"""
    r = (result or "").strip()
    if r in ("通过",):
        return "approve"
    if r in ("拒绝",):
        return "reject"
    if r in ("需补齐资料", "需补件"):
        return "supplement"
    return "other"


def normalize_manual(status):
    """标准化人工状态"""
    s = (status or "").strip()
    if s == "通过":
        return "approve"
    if s == "拒绝":
        return "reject"
    if s in ("需补齐资料", "需补件"):
        return "supplement"
    if s == "待定":
        return "pending"
    return "other"


def classify(ai_norm, manual_norm):
    """分类 P0/P1/P2/一致"""
    if ai_norm == manual_norm:
        return "consistent"
    # P0: AI通过 but 人工拒绝/补件
    if ai_norm == "approve" and manual_norm in ("reject", "supplement"):
        return "P0"
    # P1: AI补件 but 人工通过/拒绝
    if ai_norm == "supplement" and manual_norm in ("approve", "reject"):
        return "P1"
    # P2: AI拒绝 but 人工通过/补件
    if ai_norm == "reject" and manual_norm in ("approve", "supplement"):
        return "P2"
    return "other_discrepancy"


def main():
    show_samples = "--sample" in sys.argv

    conn = connect_db()
    try:
        with conn.cursor() as cur:
            # 航班延误总数
            cur.execute("SELECT COUNT(*) as cnt FROM ai_review_result WHERE claim_type = 'flight_delay'")
            total_fd = cur.fetchone()["cnt"]
            print(f"【数据库快照】航班延误总案件数: {total_fd}")
            print()

            # 拉取所有航班延误案件
            cur.execute("""
                SELECT forceid, claim_id, audit_result, manual_status, manual_conclusion,
                       payout_amount, benefit_name, created_at, updated_at, remark
                FROM ai_review_result
                WHERE claim_type = 'flight_delay'
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

            # 分类统计
            p0_cases = []
            p1_cases = []
            p2_cases = []
            consistent = []
            other_disc = []

            for row in rows:
                ai_n = normalize_audit(row["audit_result"])
                manual_n = normalize_manual(row["manual_status"])
                cat = classify(ai_n, manual_n)

                record = {
                    "forceid": row["forceid"],
                    "claim_id": row["claim_id"],
                    "audit_result": row["audit_result"],
                    "manual_status": row["manual_status"],
                    "manual_conclusion": row["manual_conclusion"],
                    "payout_amount": row["payout_amount"],
                    "benefit_name": row["benefit_name"],
                    "created_at": row["created_at"],
                    "remark": row["remark"],
                }

                if cat == "consistent":
                    consistent.append(record)
                elif cat == "P0":
                    p0_cases.append(record)
                elif cat == "P1":
                    p1_cases.append(record)
                elif cat == "P2":
                    p2_cases.append(record)
                else:
                    other_disc.append(record)

            # ── 总体统计 ──
            print("=" * 70)
            print("航班延误 AI vs 人工 总体统计")
            print("=" * 70)
            print(f"总案件数:        {total_fd}")
            print(f"一致:            {len(consistent)}  ({len(consistent)/total_fd:.1%})")
            print(f"P0 (AI通过/人工拒绝或补件):  {len(p0_cases)}  ({len(p0_cases)/total_fd:.1%})")
            print(f"P1 (AI补件/人工通过或拒绝):  {len(p1_cases)}  ({len(p1_cases)/total_fd:.1%})")
            print(f"P2 (AI拒绝/人工通过或补件):  {len(p2_cases)}  ({len(p2_cases)/total_fd:.1%})")
            print(f"其他不一致:      {len(other_disc)}  ({len(other_disc)/total_fd:.1%})")
            print()

            # ── P0 详细分析 ──
            print("=" * 70)
            print("P0: AI通过 vs 人工拒绝/补件 (最严重)")
            print("=" * 70)
            p0_manual = Counter(c["manual_status"] for c in p0_cases)
            print(f"数量: {len(p0_cases)}")
            print(f"人工状态分布: {dict(p0_manual)}")
            print()
            for c in p0_cases:
                print(f"  ForceID: {c['forceid']}")
                print(f"    AI: {c['audit_result']} | 人工: {c['manual_status']}")
                print(f"    人工备注: {c['manual_conclusion'] or '无'}")
                print(f"    AI赔付: {c['payout_amount']}")
                print()

            # ── P1 详细分析 ──
            print("=" * 70)
            print("P1: AI补件 vs 人工通过/拒绝")
            print("=" * 70)
            p1_manual = Counter(c["manual_status"] for c in p1_cases)
            print(f"数量: {len(p1_cases)}")
            print(f"人工状态分布: {dict(p1_manual)}")
            print()

            # 补件原因分析
            print(f"ForceID列表（前10个）:")
            for c in p1_cases[:10]:
                print(f"  {c['forceid']} - 人工: {c['manual_status']}, AI赔付: {c['payout_amount']}")
            print()

            # ── P2 详细分析 ──
            print("=" * 70)
            print("P2: AI拒绝 vs 人工通过/补件")
            print("=" * 70)
            p2_manual = Counter(c["manual_status"] for c in p2_cases)
            print(f"数量: {len(p2_cases)}")
            print(f"人工状态分布: {dict(p2_manual)}")
            print()

            # 拒赔原因分析（需要从本地审核结果文件获取Remark）
            # 先尝试从本地 review_results 获取
            from app.config import config
            REVIEW_DIR = config.REVIEW_RESULTS_DIR

            reject_reasons = Counter()
            p2_with_reason = []

            for c in p2_cases:
                fid = c["forceid"]
                reason = "未知"
                # 尝试从本地审核结果读取
                for rf in REVIEW_DIR.rglob(f"{fid}_ai_review.json"):
                    try:
                        rd = json.loads(rf.read_text(encoding="utf-8"))
                        remark = str(rd.get("Remark") or "")
                        if "拒赔" in remark:
                            # 简单分类
                            if "纯国内" in remark:
                                reason = "纯国内航班"
                            elif "重复索赔" in remark or "重复" in remark:
                                reason = "重复索赔"
                            elif "未达" in remark or "分钟" in remark:
                                reason = "未达起赔门槛"
                            elif "有效期" in remark or "超出" in remark:
                                reason = "超出有效期"
                            elif "中转接驳" in remark:
                                reason = "中转接驳免责"
                            else:
                                reason = "其他"
                        else:
                            reason = "非拒赔"
                    except:
                        pass
                reject_reasons[reason] += 1
                p2_with_reason.append({**c, "reject_reason": reason})

            print("P2 拒赔原因分布:")
            for reason, cnt in reject_reasons.most_common():
                print(f"  {reason}: {cnt}")
            print()

            # ── 随机抽取样例 ──
            print("=" * 70)
            print("随机抽取样例（用于深度分析）")
            print("=" * 70)

            # 从P0/P1/P2各抽2个（如果有）
            for label, cases in [("P0", p0_cases), ("P1", p1_cases), ("P2", p2_cases)]:
                if not cases:
                    print(f"\n{label}: 无案件")
                    continue
                n = min(2, len(cases))
                samples = random.sample(cases, n)
                print(f"\n{label} 随机抽取 {n} 个样例:")
                for c in samples:
                    print(f"  ForceID: {c['forceid']}")
                    print(f"    AI: {c['audit_result']} | 人工: {c['manual_status']}")
                    print(f"    人工备注: {c['manual_conclusion'] or '无'}")
                    print(f"    AI赔付: {c['payout_amount']}")

            # 输出所有forceid列表供后续重跑使用
            print()
            print("=" * 70)
            print("ForceID 列表（供重跑验证用）")
            print("=" * 70)
            print(f"\nP0 forceids: {','.join(c['forceid'] for c in p0_cases)}")
            print(f"\nP1 forceids (前20): {','.join(c['forceid'] for c in p1_cases[:20])}")
            print(f"\nP2 forceids (前20): {','.join(c['forceid'] for c in p2_cases[:20])}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
