#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量推送行李延误审核结果到前端并同步到数据库
用法: python scripts/push_baggage_all.py
"""

import sys
import json
import asyncio
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import aiohttp
import pymysql
from app.config import config
from app.output.frontend_pusher import push_to_frontend
from app.production.main_workflow import ProductionWorkflow as MainWorkflow

REVIEW_DIR = config.REVIEW_RESULTS_DIR
CLAIMS_DIR = config.CLAIMS_DATA_DIR


def _build_claim_info_cache() -> dict:
    cache = {}
    for f in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            fid = str(data.get("forceid") or "").strip()
            if fid:
                cache[fid] = data
        except Exception:
            pass
    return cache


def _sync_to_db(fields: dict) -> bool:
    try:
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", ""),
            port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "ai"),
            charset="utf8mb4",
        )
        try:
            with conn.cursor() as cur:
                keys = list(fields.keys())
                placeholders = ", ".join(["%s"] * len(keys))
                update_clause = ", ".join(
                    [f"{k}=VALUES({k})" for k in keys if k != "forceid"]
                )
                sql = (
                    f"INSERT INTO ai_review_result ({', '.join(keys)}) "
                    f"VALUES ({placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {update_clause}, updated_at=CURRENT_TIMESTAMP"
                )
                cur.execute(sql, list(fields.values()))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        print(f"  数据库写入异常: {e}")
        return False


async def push_all():
    # 获取所有行李延误审核结果
    baggage_dir = REVIEW_DIR / "baggage_delay"
    forceids = sorted([
        fn.replace("_ai_review.json", "")
        for fn in os.listdir(baggage_dir)
        if fn.endswith("_ai_review.json")
    ])
    print(f"找到 {len(forceids)} 个行李延误审核结果")

    workflow = MainWorkflow()
    claim_info_cache = _build_claim_info_cache()
    print(f"claim_info 缓存: {len(claim_info_cache)} 条\n")

    ok_frontend = ok_db = fail_frontend = fail_db = 0

    async with aiohttp.ClientSession() as session:
        for i, forceid in enumerate(forceids, 1):
            print(f"[{i}/{len(forceids)}] 处理: {forceid}")

            result_file = baggage_dir / f"{forceid}_ai_review.json"
            if not result_file.exists():
                print(f"  未找到审核结果文件，跳过")
                continue

            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  读取审核结果失败: {e}")
                continue

            remark = str(result.get("Remark", ""))[:80]
            print(f"  Remark: {remark}")

            # 推送前端
            try:
                push_result = await push_to_frontend(result, session)
                if push_result.get("success"):
                    print(f"  [OK] 推送前端成功")
                    ok_frontend += 1
                else:
                    print(f"  [FAIL] 推送前端失败: {push_result.get('response', '')[:120]}")
                    fail_frontend += 1
            except Exception as e:
                print(f"  [ERR] 推送前端异常: {e}")
                fail_frontend += 1

            # 写数据库
            try:
                claim_info = claim_info_cache.get(forceid, {})
                fields = workflow._extract_review_fields(result, claim_info)
                db_ok = _sync_to_db(fields)
                if db_ok:
                    print(f"  [OK] 数据库同步成功")
                    ok_db += 1
                else:
                    print(f"  [FAIL] 数据库同步失败")
                    fail_db += 1
            except Exception as e:
                import traceback
                print(f"  [ERR] 数据库同步异常: {e}")
                traceback.print_exc()
                fail_db += 1

    print(f"\n===== 推送完成 =====")
    print(f"前端推送成功: {ok_frontend}/{len(forceids)}")
    print(f"前端推送失败: {fail_frontend}")
    print(f"数据库同步成功: {ok_db}/{len(forceids)}")
    print(f"数据库同步失败: {fail_db}")


if __name__ == "__main__":
    asyncio.run(push_all())
