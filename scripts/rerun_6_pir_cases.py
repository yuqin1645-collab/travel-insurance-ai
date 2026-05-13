#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重跑6件PIR签收时间缺失的案件，验证二次提取修复效果。
"""

import sys
import os
import json
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.config import config
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.policy_terms_registry import POLICY_TERMS

TEST_CASES = [
    "a0nC800000IdwOPIAZ",
    "a0nC800000ICHXNIA5",
    "a0nC800000IBWjlIAH",
    "a0nC800000Ib5ZTIAZ",
    "a0nC800000I9eOxIAJ",
    "a0nC800000I9eOuIAJ",
    "a0nC800000I7iQHIAZ",
    "a0nC800000HqpJBIAZ",
    "a0nC800000JZU0NIAX",
]

CLAIMS_DIR = config.CLAIMS_DATA_DIR
REVIEW_DIR = config.REVIEW_RESULTS_DIR


def find_claim_folder(forceid: str) -> Path | None:
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                return info_file.parent
        except Exception:
            continue
    return None


def detect_claim_type(claim_info: dict) -> str:
    benefit = str(claim_info.get("BenefitName") or "")
    if "航班延误" in benefit:
        return "flight_delay"
    if "行李延误" in benefit:
        return "baggage_delay"
    return "baggage_damage"


async def rerun_single(forceid: str, session) -> tuple:
    folder = find_claim_folder(forceid)
    if not folder:
        print(f"  [未找到] {forceid}")
        return forceid, None, None

    info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
    ct = detect_claim_type(info)
    print(f"  [{ct}] {forceid}")

    try:
        terms_file = POLICY_TERMS.resolve(ct)
        policy_terms = terms_file.read_text(encoding="utf-8")
    except Exception:
        policy_terms = ""

    reviewer = AIClaimReviewer()
    for attempt in range(1, 4):
        try:
            result = await review_claim_async(
                reviewer, folder, policy_terms, 1, 1, session
            )
            return forceid, ct, result
        except Exception as e:
            print(f"    审核失败 attempt={attempt}: {e}")
            if attempt < 3:
                await asyncio.sleep(3)
    return forceid, ct, None


async def main():
    import aiohttp
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        results = []
        for i, fid in enumerate(TEST_CASES, 1):
            print(f"\n[{i}/{len(TEST_CASES)}] 重跑: {fid}")
            fid, ct, result = await rerun_single(fid, session)
            results.append((fid, ct, result))

            if result:
                audit = result.get("audit_result", "")
                remark = result.get("Remark", "")[:200]
                payout = result.get("payout_amount", "")

                debug = result.get("DebugInfo", {})
                auto_corrected = debug.get("auto_corrected", [])
                delay_calc = debug.get("delay_calc", {})
                pir_extract = debug.get("pir_receipt_extract", {})
                pir_warning = debug.get("pir_receipt_extract_warning", "")
                transfer_receipt = debug.get("transfer_flight_receipt", {})

                print(f"    audit_result: {audit}")
                print(f"    Remark: {remark}")
                print(f"    payout: {payout}")
                if auto_corrected:
                    for ac in auto_corrected:
                        print(f"    auto_corrected: {ac}")
                else:
                    print(f"    auto_corrected: (无)")
                if pir_extract:
                    print(f"    pir_receipt_extract: {json.dumps(pir_extract, ensure_ascii=False)}")
                if pir_warning:
                    print(f"    pir_warning: {pir_warning}")
                if transfer_receipt:
                    print(f"    transfer_flight_receipt: {json.dumps(transfer_receipt, ensure_ascii=False, default=str)[:200]}")
                if delay_calc:
                    print(f"    delay_calc: method={delay_calc.get('method')}, delay_hours={delay_calc.get('delay_hours')}")

                # 保存结果
                output_dir = REVIEW_DIR / ct
                output_dir.mkdir(parents=True, exist_ok=True)
                result_file = output_dir / f"{fid}_ai_review.json"
                result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"    已保存: {result_file}")
            else:
                print(f"    审核失败")

    # 汇总
    print(f"\n{'='*60}")
    print("汇总")
    print(f"{'='*60}")
    for fid, ct, r in results:
        if r is None:
            print(f"  {fid}: 审核失败")
        else:
            audit = r.get("audit_result", "")
            delay_calc = r.get("DebugInfo", {}).get("delay_calc", {})
            delay_hours = delay_calc.get("delay_hours", "unknown")
            pir = r.get("DebugInfo", {}).get("pir_receipt_extract", {})
            pir_attempted = pir.get("attempted", False)
            auto_corrected = r.get("DebugInfo", {}).get("auto_corrected", [])
            transfer_receipt = r.get("DebugInfo", {}).get("transfer_flight_receipt", {})
            ac_str = ", ".join(auto_corrected) if auto_corrected else "(无)"
            tr_str = ""
            if transfer_receipt.get("receipt_time_set"):
                tr_str = f" [转运回退成功: {transfer_receipt['receipt_time_set']}]"
            print(f"  {fid}:")
            print(f"    audit={audit}")
            print(f"    delay_hours={delay_hours}")
            print(f"    pir_extract={'attempted' if pir_attempted else 'skipped'}")
            print(f"    transfer_flight_receipt={transfer_receipt.get('receipt_time_set', '(未触发)')}{tr_str}")
            print(f"    auto_corrected: {ac_str}")


if __name__ == "__main__":
    asyncio.run(main())
