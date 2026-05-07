#!/usr/bin/env python3
"""P1 案件重跑验证 — 直接调用审核流程"""
import sys, os, asyncio, json, time
sys.path.insert(0, r'd:\UserFiles\Desktop\travel-insurance-ai-code')

from pathlib import Path
import aiohttp

from scripts.review import _review_single, _save_and_push, find_claim_folder, detect_claim_type, POLICY_TERMS

test_cases = [
    # flight_delay 人工通过 (抽3件)
    "a0nC800000JI3FuIAL",
    "a0nC800000IWNn7IAH",
    "a0nC800000JIl0jIAD",
    # flight_delay 人工拒绝 (抽2件)
    "a0nC800000JMawLIAT",
    "a0nC800000IWp9wIAD",
    # baggage_delay 人工通过 (抽3件)
    "a0nC800000IKcLLIA1",
    "a0nC800000IPZUrIAP",
    "a0nC800000IQUEfIAP",
]

async def main():
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        for i, fid in enumerate(test_cases, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(test_cases)}] Running: {fid}")
            folder = find_claim_folder(fid)
            if not folder:
                print(f"  SKIP: folder not found")
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
                result = await _review_single(folder, policy_terms, session, i, len(test_cases))
                elapsed = time.time() - t0
                if result:
                    audit = result.get('flight_delay_audit') or result.get('baggage_delay_audit') or {}
                    audit_result = audit.get('audit_result', 'N/A')
                    remark = result.get('Remark', '')[:150]
                    print(f"  [{elapsed:.1f}s] audit_result={audit_result}")
                    print(f"  Remark: {remark}")
                    # 检查 hardcheck
                    debug = result.get('DebugInfo') or {}
                    hc = debug.get('flight_delay_hardcheck') or {}
                    rm = hc.get('required_materials_check') or {}
                    if rm:
                        missing = rm.get('missing_required') or []
                        note = rm.get('note', '')
                        print(f"  materials: missing={missing}")
                        print(f"  materials_note: {note[:120]}")
                    # 保存并推送
                    await _save_and_push(result, session)
                    print(f"  -> saved & pushed to DB")
                else:
                    print(f"  [{elapsed:.1f}s] result=None (all retries failed)")
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  [{elapsed:.1f}s] ERROR: {e}")

asyncio.run(main())
