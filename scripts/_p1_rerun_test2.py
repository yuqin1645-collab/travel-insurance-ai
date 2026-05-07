#!/usr/bin/env python3
"""重跑两个失败的P1案件，验证修复后的出入境兜底"""
import sys, os, asyncio, json, time
sys.path.insert(0, r'd:\UserFiles\Desktop\travel-insurance-ai-code')

from pathlib import Path
import aiohttp

from scripts.review import _review_single, _save_and_push, find_claim_folder, detect_claim_type, POLICY_TERMS

test_cases = [
    "a0nC800000JI3FuIAL",  # MLE unknown airport - 应触发airport_unknown兜底
    "a0nC800000IWp9wIAD",  # NRT->ICN - 应触发is_international兜底
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
                    audit = result.get('flight_delay_audit') or {}
                    audit_result = audit.get('audit_result', 'N/A')
                    remark = result.get('Remark', '')[:200]
                    print(f"  [{elapsed:.1f}s] audit_result={audit_result}")
                    print(f"  Remark: {remark}")
                    debug = result.get('DebugInfo') or {}
                    hc = debug.get('flight_delay_hardcheck') or {}
                    rm = hc.get('required_materials_check') or {}
                    if rm:
                        print(f"  has_passport={rm.get('has_passport')}, has_boarding_pass={rm.get('has_boarding_pass')}")
                        print(f"  has_exit_entry_record={rm.get('has_exit_entry_record')}")
                        print(f"  is_id_card_policy={rm.get('is_id_card_policy')}")
                        print(f"  missing={rm.get('missing_required')}")
                        print(f"  note={rm.get('note','')[:150]}")
                        print(f"  debug_notes={hc.get('debug_notes', [])}")
                    # 也检查 domestic_flight_check
                    dom = hc.get('domestic_flight_check') or {}
                    if dom:
                        print(f"  domestic: is_pure_domestic_cn={dom.get('is_pure_domestic_cn')}, dep={dom.get('dep_country')}, arr={dom.get('arr_country')}")
                    await _save_and_push(result, session)
                    print(f"  -> saved & pushed")
                else:
                    print(f"  [{elapsed:.1f}s] result=None")
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  [{elapsed:.1f}s] ERROR: {e}")
                import traceback
                traceback.print_exc()

asyncio.run(main())
