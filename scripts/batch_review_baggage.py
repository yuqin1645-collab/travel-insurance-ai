#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行李延误批量审核脚本
- 对 claims_data/行李延误/ 下所有案件跑 AI 审核
- 审核结果保存到 review_results/baggage_delay/
- 完成后输出与人工审核结论的对比报告

用法:
  python scripts/batch_review_baggage.py               # 跑全部
  python scripts/batch_review_baggage.py --limit 10   # 只跑前N个
  python scripts/batch_review_baggage.py --report-only # 只生成报告（不重跑已有结果）
"""

import sys
import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict, Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import aiohttp
from app.config import config
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.policy_terms_registry import POLICY_TERMS

CLAIMS_DIR = config.CLAIMS_DATA_DIR / "行李延误"
REVIEW_DIR = config.REVIEW_RESULTS_DIR / "baggage_delay"
REVIEW_DIR.mkdir(parents=True, exist_ok=True)


def load_all_cases() -> list:
    """加载所有行李延误案件"""
    cases = []
    for f in sorted(CLAIMS_DIR.rglob("claim_info.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            forceid = str(d.get("forceid") or "").strip()
            if forceid:
                cases.append({"forceid": forceid, "folder": f.parent, "claim_info": d})
        except Exception:
            continue
    return cases


def map_human_verdict(final_status: str) -> str:
    """将人工 Final_Status 映射为 approve/reject/other"""
    if "支付" in final_status or "赔付" in final_status:
        return "approve"
    if "拒赔" in final_status or "零结" in final_status:
        return "reject"
    return "other"


def map_ai_verdict(result: dict) -> str:
    """将 AI 审核结果映射为 approve/reject/supplement"""
    audit = result.get("baggage_delay_audit") or result.get("flight_delay_audit") or {}
    ar = str(audit.get("audit_result") or "").strip()
    if ar in ("通过", "approve"):
        return "approve"
    if ar in ("拒绝", "reject"):
        return "reject"
    if ar in ("需补件", "supplement"):
        return "supplement"
    is_add = str(result.get("IsAdditional") or "N").strip().upper()
    if is_add == "Y":
        return "supplement"
    # Remark 内容判断（结构化 audit 字段缺失时的兜底）
    remark = str(result.get("Remark") or "")
    if "审核通过" in remark or "建议赔付" in remark:
        return "approve"
    if "拒赔" in remark or "拒绝" in remark:
        return "reject"
    if "需补件" in remark or "补件" in remark:
        return "supplement"
    return "unknown"


async def review_one(case: dict, reviewer: AIClaimReviewer, policy_terms: str,
                     session: aiohttp.ClientSession, index: int, total: int) -> dict:
    forceid = case["forceid"]
    folder = case["folder"]
    ci = case["claim_info"]

    result_file = REVIEW_DIR / f"{forceid}_ai_review.json"

    print(f"[{index}/{total}] {forceid} ...", end=" ", flush=True)

    try:
        result = await review_claim_async(reviewer, folder, policy_terms, 1, 1, session)
        if result:
            result_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            ai_v = map_ai_verdict(result)
            remark = str(result.get("Remark") or "")[:80]
            print(f"OK [{ai_v}] {remark}")
            return {"forceid": forceid, "status": "ok", "ai_verdict": ai_v, "result": result, "claim_info": ci}
        else:
            print("EMPTY")
            return {"forceid": forceid, "status": "empty", "ai_verdict": "unknown", "result": {}, "claim_info": ci}
    except Exception as e:
        print(f"ERR {e}")
        return {"forceid": forceid, "status": "error", "ai_verdict": "unknown", "result": {}, "claim_info": ci, "error": str(e)}


def print_report(outcomes: list):
    """输出对比报告"""
    print("\n" + "=" * 70)
    print("行李延误 AI 审核 vs 人工审核对比报告")
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    total = len(outcomes)
    errors = [o for o in outcomes if o["status"] == "error"]
    valid = [o for o in outcomes if o["status"] == "ok"]

    print(f"\n总案件: {total}，成功审核: {len(valid)}，失败: {len(errors)}")

    # 人工结论分布
    human_cnt = Counter(
        map_human_verdict(o["claim_info"].get("Final_Status") or o["claim_info"].get("final_status") or "")
        for o in valid
    )
    print(f"\n人工结论分布: 赔付={human_cnt['approve']}，拒赔/零结={human_cnt['reject']}，其他={human_cnt['other']}")

    # AI 结论分布
    ai_cnt = Counter(o["ai_verdict"] for o in valid)
    print(f"AI 结论分布:   通过={ai_cnt['approve']}，拒绝={ai_cnt['reject']}，补件={ai_cnt['supplement']}，未知={ai_cnt['unknown']}")

    # 一致性分析（排除"其他"人工结论）
    comparable = [o for o in valid if map_human_verdict(
        o["claim_info"].get("Final_Status") or o["claim_info"].get("final_status") or ""
    ) in ("approve", "reject")]

    match = 0
    mismatch_list = []
    for o in comparable:
        h = map_human_verdict(o["claim_info"].get("Final_Status") or o["claim_info"].get("final_status") or "")
        a = o["ai_verdict"]
        if h == a:
            match += 1
        else:
            mismatch_list.append(o)

    acc = match / len(comparable) * 100 if comparable else 0
    print(f"\n可对比案件: {len(comparable)}")
    print(f"结论一致:   {match} ({acc:.1f}%)")
    print(f"结论不一致: {len(mismatch_list)} ({100-acc:.1f}%)")

    # 不一致明细
    if mismatch_list:
        print(f"\n{'=' * 70}")
        print(f"不一致案件明细 ({len(mismatch_list)} 个)")
        print(f"{'=' * 70}")
        for o in mismatch_list:
            ci = o["claim_info"]
            h_raw = ci.get("Final_Status") or ci.get("final_status") or ""
            h = map_human_verdict(h_raw)
            a = o["ai_verdict"]
            audit = o["result"].get("baggage_delay_audit") or o["result"].get("flight_delay_audit") or {}
            remark = str(o["result"].get("Remark") or "")[:100]
            print(f"\n  forceid: {o['forceid']}")
            print(f"  人工: {h_raw} -> {h}  |  AI: {a}")
            print(f"  AI Remark: {remark}")
            # 关键字段
            key = audit.get("key_data") or {}
            print(f"  delay_minutes={key.get('delay_duration_minutes','-')}, payout={audit.get('payout_suggestion',{}).get('amount','-')}")

    # 按人工结论细分 AI 判断分布
    print(f"\n{'=' * 70}")
    print("交叉矩阵（行=人工结论，列=AI结论）")
    print(f"{'':12} {'approve':>10} {'reject':>10} {'supplement':>12} {'unknown':>10}")
    for h_label in ("approve", "reject", "other"):
        row = [o for o in valid if map_human_verdict(
            o["claim_info"].get("Final_Status") or o["claim_info"].get("final_status") or ""
        ) == h_label]
        cnt = Counter(o["ai_verdict"] for o in row)
        print(f"  {h_label:10} {cnt.get('approve',0):>10} {cnt.get('reject',0):>10} {cnt.get('supplement',0):>12} {cnt.get('unknown',0):>10}")

    print(f"\n{'=' * 70}")

    # 保存报告到文件
    report_file = Path("review_results/baggage_delay_comparison_report.txt")
    try:
        lines = []
        lines.append(f"行李延误 AI vs 人工审核对比报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append(f"总案件={total}, 成功={len(valid)}, 失败={len(errors)}\n")
        lines.append(f"可对比={len(comparable)}, 一致={match}({acc:.1f}%), 不一致={len(mismatch_list)}\n\n")
        lines.append("不一致案件:\n")
        for o in mismatch_list:
            ci = o["claim_info"]
            h_raw = ci.get("Final_Status") or ci.get("final_status") or ""
            remark = str(o["result"].get("Remark") or "")[:120]
            lines.append(f"  {o['forceid']}  人工={h_raw}  AI={o['ai_verdict']}  {remark}\n")
        report_file.write_text("".join(lines), encoding="utf-8")
        print(f"\n报告已保存: {report_file}")
    except Exception as e:
        print(f"保存报告失败: {e}")


async def main(limit: int = 0, report_only: bool = False, skip_existing: bool = True):
    cases = load_all_cases()
    if limit:
        cases = cases[:limit]

    print(f"行李延误案件总数: {len(cases)}")

    # 加载条款
    try:
        terms_file = POLICY_TERMS.resolve("baggage_delay")
        policy_terms = terms_file.read_text(encoding="utf-8")
        print(f"条款文件已加载: {terms_file}")
    except Exception as e:
        print(f"条款文件加载失败: {e}")
        policy_terms = ""

    outcomes = []

    if report_only:
        # 只加载已有结果生成报告
        for case in cases:
            f = REVIEW_DIR / f"{case['forceid']}_ai_review.json"
            if f.exists():
                try:
                    result = json.loads(f.read_text(encoding="utf-8"))
                    outcomes.append({
                        "forceid": case["forceid"],
                        "status": "ok",
                        "ai_verdict": map_ai_verdict(result),
                        "result": result,
                        "claim_info": case["claim_info"]
                    })
                except Exception:
                    pass
        print(f"加载已有审核结果: {len(outcomes)} 个")
    else:
        # 跑审核
        to_review = cases
        if skip_existing:
            to_review = [c for c in cases if not (REVIEW_DIR / f"{c['forceid']}_ai_review.json").exists()]
            skipped = len(cases) - len(to_review)
            if skipped:
                print(f"跳过已有审核结果: {skipped} 个")

        print(f"待审核: {len(to_review)} 个\n")

        reviewer = AIClaimReviewer()
        connector = aiohttp.TCPConnector(limit=3)
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            for i, case in enumerate(to_review, 1):
                outcome = await review_one(case, reviewer, policy_terms, session, i, len(to_review))
                outcomes.append(outcome)
                # 每10个保存一次中间报告
                if i % 10 == 0:
                    already_done = [
                        {"forceid": c["forceid"], "status": "ok",
                         "ai_verdict": map_ai_verdict(json.loads((REVIEW_DIR / f"{c['forceid']}_ai_review.json").read_text(encoding="utf-8"))),
                         "result": json.loads((REVIEW_DIR / f"{c['forceid']}_ai_review.json").read_text(encoding="utf-8")),
                         "claim_info": c["claim_info"]}
                        for c in cases
                        if (REVIEW_DIR / f"{c['forceid']}_ai_review.json").exists()
                        and c["forceid"] not in {o["forceid"] for o in outcomes}
                    ]
                    print(f"\n--- 进度 {i}/{len(to_review)} ---")

        # 合并：把已跳过的也加进来
        if skip_existing:
            for case in cases:
                if case["forceid"] not in {o["forceid"] for o in outcomes}:
                    f = REVIEW_DIR / f"{case['forceid']}_ai_review.json"
                    if f.exists():
                        try:
                            result = json.loads(f.read_text(encoding="utf-8"))
                            outcomes.append({
                                "forceid": case["forceid"], "status": "ok",
                                "ai_verdict": map_ai_verdict(result),
                                "result": result, "claim_info": case["claim_info"]
                            })
                        except Exception:
                            pass

    print_report(outcomes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前N个案件（0=全部）")
    parser.add_argument("--report-only", action="store_true", help="只生成报告，不重跑审核")
    parser.add_argument("--rerun-all", action="store_true", help="重跑所有案件（包含已有结果）")
    args = parser.parse_args()

    asyncio.run(main(
        limit=args.limit,
        report_only=args.report_only,
        skip_existing=not args.rerun_all,
    ))
