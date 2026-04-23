#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强制重跑指定 forceid 的 AI 审核，忽略 Final_Status 过滤。

用法:
  venv\Scripts\python.exe scripts\rerun_claims.py a0nC800000KScEwIAL a0nC800000LHgZtIAL a0nC800000LOW4LIAX
"""

import sys
import os
import json
import asyncio
import aiohttp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.config import config
from app.logging_utils import LOGGER, log_extra as _log_extra
from app.policy_terms_registry import POLICY_TERMS
from app.output.frontend_pusher import push_to_frontend
from scripts.sync_review_to_db import sync_review_to_db_for_forceid

CLAIMS_DIR = config.CLAIMS_DATA_DIR
REVIEW_DIR = config.REVIEW_RESULTS_DIR


def find_claim_folder(forceid: str) -> Path:
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "").strip() == forceid:
                return info_file.parent
        except Exception:
            continue
    return None


def detect_claim_type(claim_info: dict) -> str:
    benefit = str(claim_info.get("BenefitName") or "")
    folder_hint = str(claim_info.get("_claim_folder_path") or "")
    combined = f"{benefit} {folder_hint}"
    if "行李延误" in combined:
        return "baggage_delay"
    if "航班延误" in combined or "flight_delay" in combined.lower():
        return "flight_delay"
    return "baggage_damage"


async def rerun(forceids: list, dry_run: bool = False):
    reviewer = AIClaimReviewer()
    policy_terms_cache = {}

    async with aiohttp.ClientSession() as session:
        for i, forceid in enumerate(forceids, 1):
            print(f"\n[{i}/{len(forceids)}] 重跑: {forceid}")

            folder = find_claim_folder(forceid)
            if not folder:
                print(f"  未找到案件目录，跳过")
                continue

            info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
            claim_type = detect_claim_type(info)

            if claim_type not in policy_terms_cache:
                try:
                    terms_file = POLICY_TERMS.resolve(claim_type)
                    policy_terms_cache[claim_type] = terms_file.read_text(encoding="utf-8")
                except Exception as e:
                    print(f"  条款文件读取失败: {e}")
                    policy_terms_cache[claim_type] = ""

            result = None
            for attempt in range(1, 4):
                try:
                    result = await review_claim_async(
                        reviewer, folder, policy_terms_cache[claim_type],
                        i, len(forceids), session
                    )
                    break
                except Exception as e:
                    print(f"  审核失败 attempt={attempt}: {e}")
                    if attempt < 3:
                        await asyncio.sleep(3)

            if not result:
                print(f"  审核彻底失败，跳过")
                continue

            # 保存审核结果
            output_dir = REVIEW_DIR / claim_type
            output_dir.mkdir(parents=True, exist_ok=True)
            result_file = output_dir / f"{result['forceid']}_ai_review.json"
            result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  审核结果已保存: {result_file.name}")
            audit_block = result.get("baggage_delay_audit") or result.get("flight_delay_audit") or {}
            print(f"  audit_result: {audit_block.get('audit_result', '')}")

            if dry_run:
                print(f"  [dry-run] 跳过推送和数据库同步")
            else:
                # 1. 推送前端
                push_result = await push_to_frontend(result, session)
                if push_result.get("success"):
                    print(f"  推送成功")
                else:
                    print(f"  推送失败: {push_result.get('response', '')[:100]}")

                # 2. 同步数据库
                db_ok = sync_review_to_db_for_forceid(result)
                if db_ok:
                    print(f"  数据库同步成功")
                else:
                    print(f"  数据库同步失败")

    print("\n重跑完成")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("forceids", nargs="+", help="要重跑的 forceid 列表")
    parser.add_argument("--dry-run", action="store_true", help="不推送前端")
    args = parser.parse_args()
    asyncio.run(rerun(args.forceids, dry_run=args.dry_run))
