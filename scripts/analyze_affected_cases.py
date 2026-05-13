#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计涉及5条业务规则修改的航班延误案件

规则2: 单程改多程 → 机场匹配阻断移除
规则3: 多次改签 → 登机牌优先
规则4: 联程多程 → 末程机场
规则5: 中转接驳 → 因果检查优先

筛选策略：
- P0/P2案件（AI与人工不一致）
- 从本地审核结果中提取特征：改签、联程、中转接驳等关键词
"""

import os
import sys
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

import pymysql
from app.config import config

REVIEW_DIR = config.REVIEW_RESULTS_DIR


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
    r = (result or "").strip()
    if r == "通过": return "approve"
    if r == "拒绝": return "reject"
    if r in ("需补齐资料", "需补件"): return "supplement"
    return "other"


def normalize_manual(status):
    s = (status or "").strip()
    if s == "通过": return "approve"
    if s == "拒绝": return "reject"
    if s in ("需补齐资料", "需补件"): return "supplement"
    if s == "待定": return "pending"
    return "other"


def classify(ai_n, manual_n):
    if ai_n == manual_n: return "consistent"
    if ai_n == "approve" and manual_n in ("reject", "supplement"): return "P0"
    if ai_n == "supplement" and manual_n in ("approve", "reject"): return "P1"
    if ai_n == "reject" and manual_n in ("approve", "supplement"): return "P2"
    return "other"


def load_local_review(fid):
    """从本地审核结果加载详细数据"""
    for rf in REVIEW_DIR.rglob(f"{fid}_ai_review.json"):
        try:
            return json.loads(rf.read_text(encoding="utf-8"))
        except:
            pass
    return None


def analyze_features(review_data):
    """分析审核结果中的特征，判断是否涉及规则修改"""
    if not review_data:
        return {"has_data": False}

    features = {"has_data": True, "rule_hits": []}

    debug = review_data.get("DebugInfo") or {}
    fd_parse = debug.get("flight_delay_parse") or {}
    fd_hardcheck = debug.get("flight_delay_hardcheck") or {}
    fd_parse_enriched = debug.get("flight_delay_parse_enriched") or {}
    delay_calc = fd_parse_enriched.get("computed_delay") or {}

    itinerary = fd_parse.get("itinerary") or {}
    alternate_local = fd_parse.get("alternate_local") or {}
    schedule_local = fd_parse.get("schedule_local") or {}

    # 规则2/3特征：存在改签航班
    has_alt = bool(alternate_local.get("alt_dep") and str(alternate_local.get("alt_dep")) not in ("", "unknown"))
    alt_fn = str(alternate_local.get("alt_flight_no") or "")
    chain = fd_parse.get("schedule_revision_chain") or []

    if has_alt or len(chain) > 1:
        features["rule_hits"].append("rule2_rule3_rebooking")
        features["has_rebooking"] = True
        features["alt_flight_no"] = alt_fn
        features["chain_length"] = len(chain)

    # 规则2特征：机场不匹配
    orig_dep = str((fd_parse.get("route") or {}).get("dep_iata") or "").upper()
    orig_arr = str((fd_parse.get("route") or {}).get("arr_iata") or "").upper()
    alt_dep_iata = str(alternate_local.get("alt_dep_iata") or "").upper()
    alt_arr_iata = str(alternate_local.get("alt_arr_iata") or "").upper()

    if has_alt and alt_dep_iata and alt_dep_iata != orig_dep:
        features["rule_hits"].append("rule2_airport_mismatch_dep")
        features["dep_mismatch"] = f"{orig_dep} vs {alt_dep_iata}"
    if has_alt and alt_arr_iata and alt_arr_iata != orig_arr:
        features["rule_hits"].append("rule2_airport_mismatch_arr")
        features["arr_mismatch"] = f"{orig_arr} vs {alt_arr_iata}"

    # 规则4特征：联程场景
    is_connecting = itinerary.get("is_connecting_or_transit") in ("true", True)
    last_seg_dep = schedule_local.get("last_seg_dep_iata")
    last_seg_arr = schedule_local.get("last_seg_arr_iata")

    if is_connecting:
        features["rule_hits"].append("rule4_connecting")
        features["is_connecting"] = True
        if last_seg_dep:
            features["last_seg"] = f"{last_seg_dep} -> {last_seg_arr}"

    # 规则5特征：中转接驳
    missed_conn = fd_hardcheck.get("missed_connection_check") or {}
    is_missed = missed_conn.get("is_missed_connection")
    rebooking_override = missed_conn.get("rebooking_override")
    prev_seg_ok = missed_conn.get("prev_seg_arrived_ok")

    if is_connecting and (has_alt or rebooking_override or prev_seg_ok is not None):
        features["rule_hits"].append("rule5_missed_connection")
        features["missed_connection"] = is_missed
        features["rebooking_override"] = rebooking_override
        features["prev_seg_arrived_ok"] = prev_seg_ok

    # 延误计算相关
    delay_method = delay_calc.get("method", "")
    delay_minutes = delay_calc.get("final_minutes")

    features["delay_method"] = delay_method
    features["delay_minutes"] = delay_minutes

    return features


def main():
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT forceid, claim_id, audit_result, manual_status, manual_conclusion,
                       payout_amount, benefit_name, remark
                FROM ai_review_result
                WHERE claim_type = 'flight_delay'
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

        print(f"【数据库】航班延误总案件: {len(rows)}")
        print()

        # 分类
        affected_cases = []  # 涉及规则修改的案件
        all_cases = []

        for i, row in enumerate(rows):
            ai_n = normalize_audit(row["audit_result"])
            manual_n = normalize_manual(row["manual_status"])
            cat = classify(ai_n, manual_n)

            # 只关注不一致的案件（P0/P1/P2）
            if cat not in ("P0", "P1", "P2", "other"):
                continue

            fid = row["forceid"]
            review = load_local_review(fid)
            features = analyze_features(review)

            record = {
                "forceid": fid,
                "category": cat,
                "ai_result": row["audit_result"],
                "manual_status": row["manual_status"],
                "payout": row["payout_amount"],
                "remark": row["remark"] or "",
                "manual_conclusion": row["manual_conclusion"] or "",
                "features": features,
            }

            all_cases.append(record)

            # 筛选涉及规则修改的案件
            rule_hits = features.get("rule_hits", [])
            if rule_hits:
                affected_cases.append(record)

        # 统计
        print("=" * 70)
        print("涉及规则修改的案件统计")
        print("=" * 70)
        print(f"P0+P1+P2 总数: {len(all_cases)}")
        print(f"涉及规则修改: {len(affected_cases)}")
        print()

        # 按规则分类统计
        rule_stats = Counter()
        for c in affected_cases:
            for rh in c["features"]["rule_hits"]:
                rule_stats[rh] += 1

        print("规则命中分布:")
        for rh, cnt in rule_stats.most_common():
            label = {
                "rule2_rule3_rebooking": "规则2/3: 改签场景",
                "rule2_airport_mismatch_dep": "规则2: 出发机场不匹配",
                "rule2_airport_mismatch_arr": "规则2: 到达机场不匹配",
                "rule4_connecting": "规则4: 联程场景",
                "rule5_missed_connection": "规则5: 中转接驳",
            }.get(rh, rh)
            print(f"  {label}: {cnt}")
        print()

        # 按P0/P1/P2分类
        p0_affected = [c for c in affected_cases if c["category"] == "P0"]
        p1_affected = [c for c in affected_cases if c["category"] == "P1"]
        p2_affected = [c for c in affected_cases if c["category"] == "P2"]

        print(f"P0 涉及规则修改: {len(p0_affected)}")
        print(f"P1 涉及规则修改: {len(p1_affected)}")
        print(f"P2 涉及规则修改: {len(p2_affected)}")
        print()

        # 输出所有forceid（供重跑用）
        all_affected_fids = [c["forceid"] for c in affected_cases]

        # 保存结果到JSON
        output = {
            "total_flight_delay": len(rows),
            "total_discrepancy": len(all_cases),
            "total_affected": len(affected_cases),
            "rule_stats": dict(rule_stats),
            "p0_affected": len(p0_affected),
            "p1_affected": len(p1_affected),
            "p2_affected": len(p2_affected),
            "all_affected_forceids": all_affected_fids,
            "p0_forceids": [c["forceid"] for c in p0_affected],
            "p1_forceids": [c["forceid"] for c in p1_affected],
            "p2_forceids": [c["forceid"] for c in p2_affected],
            "p0_details": [],
            "p2_details": [],
        }

        # 输出P0详情
        print("=" * 70)
        print("P0 涉及规则修改案件详情")
        print("=" * 70)
        for c in p0_affected:
            f = c["features"]
            print(f"  {c['forceid']}")
            print(f"    AI: {c['ai_result']} | 人工: {c['manual_status']} | 赔付: {c['payout']}")
            print(f"    规则命中: {', '.join(f['rule_hits'])}")
            if f.get("dep_mismatch"):
                print(f"    出发机场: {f['dep_mismatch']}")
            if f.get("arr_mismatch"):
                print(f"    到达机场: {f['arr_mismatch']}")
            if f.get("last_seg"):
                print(f"    末程机场: {f['last_seg']}")
            if f.get("missed_connection") is not None:
                print(f"    中转接驳: is_missed={f['missed_connection']}, override={f.get('rebooking_override')}")
            print(f"    延误计算: {f.get('delay_method', 'unknown')} = {f.get('delay_minutes', 'unknown')}分钟")
            output["p0_details"].append({
                "forceid": c["forceid"],
                "ai_result": c["ai_result"],
                "manual_status": c["manual_status"],
                "payout": c["payout"],
                "rule_hits": f["rule_hits"],
                "delay_minutes": f.get("delay_minutes"),
            })
            print()

        # 输出P2前20个详情
        print("=" * 70)
        print("P2 涉及规则修改案件（前20个）")
        print("=" * 70)
        for c in p2_affected[:20]:
            f = c["features"]
            print(f"  {c['forceid']}")
            print(f"    AI: {c['ai_result']} | 人工: {c['manual_status']} | 赔付: {c['payout']}")
            print(f"    规则命中: {', '.join(f['rule_hits'])}")
            print(f"    延误计算: {f.get('delay_method', 'unknown')} = {f.get('delay_minutes', 'unknown')}分钟")
            output["p2_details"].append({
                "forceid": c["forceid"],
                "ai_result": c["ai_result"],
                "manual_status": c["manual_status"],
                "payout": c["payout"],
                "rule_hits": f["rule_hits"],
                "delay_minutes": f.get("delay_minutes"),
            })
            print()

        # 处理Decimal类型
        def _fix_decimal(obj):
            if isinstance(obj, dict):
                return {k: _fix_decimal(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_fix_decimal(v) for v in obj]
            if hasattr(obj, '__float__'):
                return float(obj)
            return obj
        output = _fix_decimal(output)

        # 保存JSON
        output_path = ROOT / "docs" / "affected_cases_20260511.json"
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n详细结果已保存到: {output_path}")

        # 输出重跑命令
        print(f"\n重跑命令示例:")
        print(f"  python scripts/review.py --forceid {' '.join(all_affected_fids[:5])}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
