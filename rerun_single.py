#!/usr/bin/env python3
import sys, json, asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.config import config
from app.policy_terms_registry import POLICY_TERMS

CLAIMS_DIR = config.CLAIMS_DATA_DIR
REVIEW_DIR = config.REVIEW_RESULTS_DIR

def find_claim_folder(forceid: str):
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "").strip() == forceid:
                return info_file.parent
        except Exception:
            continue
    return None

async def main():
    forceid = "a0nC800000Ll6vVIAR"
    folder = find_claim_folder(forceid)
    if not folder:
        print(f"未找到案件目录: {forceid}")
        return

    info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
    benefit = str(info.get("BenefitName") or "")
    claim_type = "flight_delay" if "航班延误" in benefit else "baggage_damage"

    terms_file = POLICY_TERMS.resolve(claim_type)
    policy_terms = terms_file.read_text(encoding="utf-8")

    reviewer = AIClaimReviewer()
    async with aiohttp.ClientSession(trust_env=True) as session:
        result = await review_claim_async(
            reviewer, folder, policy_terms,
            index=1, total=1, session=session
        )

    out_dir = REVIEW_DIR / claim_type
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{forceid}_ai_review.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存: {out_file}")
    print(f"audit_result: {result.get('flight_delay_audit', {}).get('audit_result')}")
    print(f"delay_minutes: {result.get('flight_delay_audit', {}).get('key_data', {}).get('delay_duration_minutes')}")

if __name__ == "__main__":
    import aiohttp
    asyncio.run(main())
