#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析72个行李延误重跑案件的审核结果
统计 audit_result 分布、auto_corrected 情况、补件原因分析
"""

import json
import os
from pathlib import Path
from collections import Counter, defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
RESULTS_DIR = BASE_DIR / "review_results" / "baggage_delay"
TOP_N = 72


def load_latest_results(results_dir, top_n):
    """按修改时间取最新的N个案件"""
    files = list(results_dir.glob("*_ai_review.json"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    latest = files[:top_n]

    records = []
    for f in latest:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            data["_filename"] = f.name
            data["_mtime"] = f.stat().st_mtime
            records.append(data)

    return records


def analyze(records):
    out = []
    out.append("=" * 80)
    out.append("行李延误72件重跑分析报告")
    out.append("=" * 80)
    out.append("")

    # 1. audit_result 分布
    out.append("-" * 60)
    out.append("【1】72件案件 audit_result 分布")
    out.append("-" * 60)
    result_counter = Counter()
    for r in records:
        audit = r.get("baggage_delay_audit", {})
        res = audit.get("audit_result", "未知")
        result_counter[res] += 1
    for res, cnt in result_counter.most_common():
        pct = cnt / len(records) * 100
        out.append(f"  {res}: {cnt} 件 ({pct:.1f}%)")
    out.append(f"  合计: {len(records)} 件")
    out.append("")

    # 2. auto_corrected 分布
    out.append("-" * 60)
    out.append("【2】auto_corrected 统计")
    out.append("-" * 60)
    auto_corrected_records = []
    no_auto_corrected_records = []
    for r in records:
        dbg = r.get("DebugInfo", {})
        ac = dbg.get("auto_corrected")
        if ac and len(ac) > 0:
            auto_corrected_records.append(r)
        else:
            no_auto_corrected_records.append(r)

    out.append(f"  auto_corrected=True:  {len(auto_corrected_records)} 件")
    out.append(f"  auto_corrected=False/无: {len(no_auto_corrected_records)} 件")
    out.append("")

    # 3. auto_corrected=True 案件的 audit_result 分布
    out.append("-" * 60)
    out.append("【3】auto_corrected=True 案件的 audit_result 分布")
    out.append("-" * 60)
    ac_result_counter = Counter()
    for r in auto_corrected_records:
        audit = r.get("baggage_delay_audit", {})
        res = audit.get("audit_result", "未知")
        ac_result_counter[res] += 1
    for res, cnt in ac_result_counter.most_common():
        pct = cnt / len(auto_corrected_records) * 100 if auto_corrected_records else 0
        out.append(f"  {res}: {cnt} 件 ({pct:.1f}%)")
    out.append("")

    # 4. auto_corrected=True 但 audit_result != "通过" 的案件
    out.append("-" * 60)
    out.append("【4】auto_corrected=True 但仍需补件/拒赔的案件（按补件原因分组）")
    out.append("-" * 60)

    ac_not_pass = [
        r for r in auto_corrected_records
        if r.get("baggage_delay_audit", {}).get("audit_result") != "通过"
    ]
    out.append(f"  共 {len(ac_not_pass)} 件")
    out.append("")

    # 按 Remark 分组
    reason_groups = defaultdict(list)
    for r in ac_not_pass:
        remark = r.get("Remark", "无备注")
        reason_groups[remark].append(r)

    for idx, (remark, group) in enumerate(sorted(reason_groups.items(), key=lambda x: -len(x[1])), 1):
        out.append(f"  --- 补件原因 #{idx} ({len(group)} 件) ---")
        out.append(f"  Remark: {remark}")
        out.append("")
        for r in group:
            forceid = r.get("forceid", "N/A")
            audit = r.get("baggage_delay_audit", {})
            audit_res = audit.get("audit_result", "未知")
            dbg = r.get("DebugInfo", {})
            ac_list = dbg.get("auto_corrected", []) or []
            ai = dbg.get("ai_parsed", {})
            vision = dbg.get("vision_extract", {})
            missing = dbg.get("missing_materials", [])
            pir = None  # PIR_reports 字段不存在，PIR 信息在 auto_corrected 字符串中

            out.append(f"    forceid: {forceid}")
            out.append(f"    audit_result: {audit_res}")
            out.append(f"    auto_corrected 内容: {ac_list}")

            # 关键材料字段
            out.append(f"    【ai_parsed 材料字段】")
            out.append(f"      has_baggage_tag_proof: {ai.get('has_baggage_tag_proof')}")
            out.append(f"      has_baggage_delay_proof: {ai.get('has_baggage_delay_proof')}")
            out.append(f"      has_baggage_receipt_time_proof: {ai.get('has_baggage_receipt_time_proof')}")
            out.append(f"      has_boarding_or_ticket: {ai.get('has_boarding_or_ticket')}")
            out.append(f"      has_airline_baggage_record: {ai.get('has_airline_baggage_record')}")
            out.append(f"      has_id_proof: {ai.get('has_id_proof')}")
            out.append(f"      has_passport: {ai.get('has_passport')}")
            out.append(f"      has_exit_entry_record: {ai.get('has_exit_entry_record')}")
            out.append(f"      delay_hours: {ai.get('delay_hours')}")

            out.append(f"    【vision_extract 材料字段】")
            out.append(f"      has_baggage_tag_proof: {vision.get('has_baggage_tag_proof')}")
            out.append(f"      has_baggage_delay_proof: {vision.get('has_baggage_delay_proof')}")
            out.append(f"      has_baggage_receipt_time_proof: {vision.get('has_baggage_receipt_time_proof')}")
            out.append(f"      has_boarding_or_ticket: {vision.get('has_boarding_or_ticket')}")

            out.append(f"    【其他】")
            out.append(f"      missing_materials: {missing}")
            out.append(f"      auto_corrected(from DebugInfo): {ac_list}")
            out.append("")
        out.append("")

    # 5. auto_corrected=False 案件的 audit_result 分布
    out.append("-" * 60)
    out.append("【5】auto_corrected=False/无 案件的 audit_result 分布")
    out.append("-" * 60)
    no_ac_result_counter = Counter()
    for r in no_auto_corrected_records:
        audit = r.get("baggage_delay_audit", {})
        res = audit.get("audit_result", "未知")
        no_ac_result_counter[res] += 1
    for res, cnt in no_ac_result_counter.most_common():
        pct = cnt / len(no_auto_corrected_records) * 100 if no_auto_corrected_records else 0
        out.append(f"  {res}: {cnt} 件 ({pct:.1f}%)")
    out.append("")

    # 6. auto_corrected=True 但补件的案件中，KeyConclusions 明细
    out.append("-" * 60)
    out.append("【6】auto_corrected=True 但补件案件的 KeyConclusions 明细")
    out.append("-" * 60)
    for r in ac_not_pass:
        forceid = r.get("forceid", "N/A")
        key_conclusions = r.get("KeyConclusions", [])
        out.append(f"  forceid: {forceid}")
        for kc in key_conclusions:
            cp = kc.get("checkpoint", "N/A")
            elig = kc.get("Eligible", "N/A")
            km = kc.get("Remark", "N/A")
            out.append(f"    {cp}: {elig} - {km}")
        out.append("")

    # 7. 汇总摘要
    out.append("=" * 80)
    out.append("【汇总摘要】")
    out.append("=" * 80)
    passed_count = sum(
        1 for r in records
        if r.get("baggage_delay_audit", {}).get("audit_result") == "通过"
    )
    supplement_count = sum(
        1 for r in records
        if r.get("baggage_delay_audit", {}).get("audit_result") == "需补件"
    )
    reject_count = sum(
        1 for r in records
        if r.get("baggage_delay_audit", {}).get("audit_result") in ("拒赔", "拒绝")
    )
    ac_passed = sum(
        1 for r in auto_corrected_records
        if r.get("baggage_delay_audit", {}).get("audit_result") == "通过"
    )
    ac_not_passed_count = len(ac_not_pass)

    out.append(f"  总案件数: {len(records)}")
    out.append(f"  通过: {passed_count} ({passed_count/len(records)*100:.1f}%)")
    out.append(f"  需补件: {supplement_count} ({supplement_count/len(records)*100:.1f}%)")
    out.append(f"  拒赔: {reject_count} ({reject_count/len(records)*100:.1f}%)")
    out.append("")
    out.append(f"  auto_corrected=True 共 {len(auto_corrected_records)} 件")
    out.append(f"    其中通过: {ac_passed} 件")
    out.append(f"    其中需补件/拒赔: {ac_not_passed_count} 件")
    out.append(f"  auto_corrected=False/无 共 {len(no_auto_corrected_records)} 件")
    out.append("")

    return "\n".join(out)


def main():
    records = load_latest_results(RESULTS_DIR, TOP_N)
    print(f"共读取 {len(records)} 个案件文件")
    result = analyze(records)
    print(result)

    # 保存结果
    output_path = BASE_DIR / "docs" / "72_rerun_analysis_result.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"\n分析报告已保存至: {output_path}")


if __name__ == "__main__":
    main()
