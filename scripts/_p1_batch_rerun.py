#!/usr/bin/env python3
"""批量重跑全部 P1 案件（87件）并推送到数据库"""
import sys, os, asyncio, json, time
sys.path.insert(0, r'd:\UserFiles\Desktop\travel-insurance-ai-code')

from pathlib import Path
import aiohttp

from scripts.review import _review_single, _save_and_push, find_claim_folder, detect_claim_type, POLICY_TERMS

# 全部87件P1案件
ALL_P1 = [
    # baggage_delay (29件)
    "a0nC800000IKcLLIA1", "a0nC800000IPZUrIAP", "a0nC800000IQUEfIAP",
    "a0nC800000IRuwcIAD", "a0nC800000IVGPVIA5", "a0nC800000IWCN5IAP",
    "a0nC800000IWPTxIAP", "a0nC800000Ik6ODIAZ", "a0nC800000IvVMTIA3",
    "a0nC800000IzfLeIAJ", "a0nC800000J1iVZIAZ", "a0nC800000JFFOqIAP",
    "a0nC800000JLlaUIAT", "a0nC800000JMY05IAH", "a0nC800000JUIXeIAP",
    "a0nC800000JZWF1IAP", "a0nC800000JeY4vIAF", "a0nC800000KFrYLIA1",
    "a0nC800000KOm6QIAT", "a0nC800000L1SxSIAV", "a0nC800000NGCUbIAP",
    "a0nC800000IBbl3IAD", "a0nC800000IBkDFIA1", "a0nC800000ID4ufIAD",
    "a0nC800000ID52jIAD", "a0nC800000IF4NXIA1", "a0nC800000IGDVCIA5",
    "a0nC800000IGyc9IAD", "a0nC800000IIkV3IAL",
    # flight_delay 人工拒绝 (10件)
    "a0nC800000JMawLIAT", "a0nC800000IWp9wIAD", "a0nC800000J9EbwIAF",
    "a0nC800000IsO7sIAF", "a0nC800000IbYy3IAF", "a0nC800000HOCPdIAP",
    "a0nC800000ICShPIAX", "a0nC800000JxF3KIAV", "a0nC800000JxH8NIAV",
    "a0nC800000I93VuIAJ",
    # flight_delay 人工通过 (48件)
    "a0nC800000JI3FuIAL", "a0nC800000IWNn7IAH", "a0nC800000JIl0jIAD",
    "a0nC800000LxgVNIAZ", "a0nC800000LxhzKIAR", "a0nC800000MrMFTIA3",
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
        for i, fid in enumerate(ALL_P1, 1):
            print(f"[{i}/{len(ALL_P1)}] {fid} ... ", end="", flush=True)
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
                result = await _review_single(folder, policy_terms, session, i, len(ALL_P1))
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

    print(f"\n===== P1 批量重跑完成 =====")
    print(f"通过: {stats.get('通过',0)}, 拒绝: {stats.get('拒绝',0)}, 需补齐资料: {stats.get('需补齐资料',0)}")
    print(f"错误: {stats['error']}, 跳过: {stats['skipped']}")

asyncio.run(main())
