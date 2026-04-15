#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只推送已存在的审核结果到前端并同步到数据库（不重新审核）

用法:
  python scripts/push_existing_results.py a0nC800000Lvue6IAB a0nC800000Lo2KPIAZ ...
"""

import sys
import json
import asyncio
import aiohttp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.config import config
from app.output.frontend_pusher import push_to_frontend
from scripts.sync_review_to_db import sync_review_to_db_for_forceid

REVIEW_DIR = config.REVIEW_RESULTS_DIR


async def push_and_sync(forceids: list):
    async with aiohttp.ClientSession() as session:
        for i, forceid in enumerate(forceids, 1):
            print(f"\n[{i}/{len(forceids)}] 处理: {forceid}")

            # 查找审核结果文件
            result_file = None
            for claim_type in ["flight_delay", "baggage_damage"]:
                candidate = REVIEW_DIR / claim_type / f"{forceid}_ai_review.json"
                if candidate.exists():
                    result_file = candidate
                    break

            if not result_file:
                print(f"  未找到审核结果文件，跳过")
                continue

            # 加载审核结果
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  读取审核结果失败: {e}")
                continue

            print(f"  audit_result: {result.get('flight_delay_audit', {}).get('audit_result', 'unknown')}")

            # 1. 推送前端
            try:
                push_result = await push_to_frontend(result, session)
                if push_result.get("success"):
                    print(f"  推送成功")
                else:
                    print(f"  推送失败: {push_result.get('response', '')[:100]}")
            except Exception as e:
                print(f"  推送异常: {e}")

            # 2. 同步数据库
            try:
                db_ok = sync_review_to_db_for_forceid(result)
                if db_ok:
                    print(f"  数据库同步成功")
                else:
                    print(f"  数据库同步失败")
            except Exception as e:
                print(f"  数据库同步异常: {e}")

    print("\n全部处理完成")


if __name__ == "__main__":
    forceids = sys.argv[1:]
    if not forceids:
        print("请指定要处理的 forceid")
        sys.exit(1)
    asyncio.run(push_and_sync(forceids))
