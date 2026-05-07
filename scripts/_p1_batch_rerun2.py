#!/usr/bin/env python3
"""P1批量重跑续跑 — 处理剩余42件"""
import sys, os, asyncio, json, time
sys.path.insert(0, r'd:\UserFiles\Desktop\travel-insurance-ai-code')

from pathlib import Path
import aiohttp

from scripts.review import _review_single, _save_and_push, find_claim_folder, detect_claim_type, POLICY_TERMS

REMAINING = [
    "a0nC800000IEf2XIAT", "a0nC800000MsGpXIAV", "a0nC800000MoNj8IAF",
    "a0nC800000JA2dYIAT", "a0nC800000IG4dGIAT", "a0nC800000IsoQWIAZ",
    "a0nC800000MY6fpIAD", "a0nC800000M6q8wIAB", "a0nC800000IoENCIA3",
    "a0nC800000IH21SIAT", "a0nC800000JVq5uIAD", "a0nC800000IuKoBIAV",
    "a0nC800000LQmPuIAL", "a0nC800000LR0MPIA1", "a0nC800000NPcmVIAT",
    "a0nC800000IvlkcIAB", "a0nC800000IccjRIAR", "a0nC800000LSbmwIAD",
    "a0nC800000JkparIAB", "a0nC800000IxpSLIAZ", "a0nC800000LOW4LIAX",
    "a0nC800000IxpSMIAZ", "a0nC800000LPJY7IAP", "a0nC800000IxpSNIAZ",
    "a0nC800000JmEyfIAF", "a0nC800000IBiOLIA1", "a0nC800000I0dDXIAZ",
    "a0nC800000I0zypIAB", "a0nC800000L9bM1IAJ", "a0nC800000I4RAXIA3",
    "a0nC800000N4DxVIAV", "a0nC800000I5q8TIAR", "a0nC800000I5vrJIAR",
    "a0nC800000M1QQQIA3", "a0nC800000MtcuTIAR", "a0nC800000LP6z3IAD",
    "a0nC800000IR0EWIA1", "a0nC800000IBVNuIAP", "a0nC800000I9eP3IAJ",
    "a0nC800000MWHyjIAH", "a0nC800000JFsd9IAD", "a0nC800000JGJLYIA5",
]

async def main():
    stats = {"通过": 0, "拒绝": 0, "需补齐资料": 0, "error": 0, "skipped": 0}
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        for i, fid in enumerate(REMAINING, 1):
            print(f"[{i}/{len(REMAINING)}] {fid} ... ", end="", flush=True)
            folder = find_claim_folder(fid)
            if not folder:
                print("SKIP (no folder)")
                stats["skipped"] += 1
                continue
            info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
            ct = detect_claim_type(info)
            try:
                terms_file = POLICY_TERMS.resolve(ct)
                policy_terms = terms_file.read_text(encoding="utf-8")
            except Exception:
                policy_terms = ""

            t0 = time.time()
            try:
                result = await _review_single(folder, policy_terms, session, i, len(REMAINING))
                elapsed = time.time() - t0
                if result:
                    audit = result.get('flight_delay_audit') or result.get('baggage_delay_audit') or {}
                    audit_result = audit.get('audit_result', 'N/A')
                    stats[audit_result] = stats.get(audit_result, 0) + 1
                    print(f"{audit_result} ({elapsed:.0f}s)")
                    await _save_and_push(result, session)
                else:
                    print(f"FAIL ({elapsed:.0f}s)")
                    stats["error"] += 1
            except Exception as e:
                elapsed = time.time() - t0
                print(f"ERROR ({elapsed:.0f}s): {e}")
                stats["error"] += 1

    print(f"\n===== P1 续跑完成 =====")
    print(f"通过: {stats.get('通过',0)}, 拒绝: {stats.get('拒绝',0)}, 需补齐资料: {stats.get('需补齐资料',0)}")
    print(f"错误: {stats['error']}, 跳过: {stats['skipped']}")

asyncio.run(main())
