#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
只推送已存在的审核结果到前端并同步到数据库（不重新审核）
使用 main_workflow._extract_review_fields 写入完整字段（含新增航班场景/飞常准字段）

用法:
  python scripts/push_existing_results.py a0nC800000Lvue6IAB a0nC800000Lo2KPIAZ ...
"""

import sys
import json
import asyncio
import os
import aiohttp
import pymysql
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.config import config
from app.output.frontend_pusher import push_to_frontend
from app.production.main_workflow import ProductionWorkflow as MainWorkflow

REVIEW_DIR = config.REVIEW_RESULTS_DIR
CLAIMS_DIR = config.CLAIMS_DATA_DIR


def _build_claim_info_cache() -> dict:
    """构建 forceid -> claim_info 缓存"""
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
    """用完整字段写入数据库（INSERT ... ON DUPLICATE KEY UPDATE）"""
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


async def push_and_sync(forceids: list):
    workflow = MainWorkflow()
    claim_info_cache = _build_claim_info_cache()
    print(f"claim_info 缓存: {len(claim_info_cache)} 条")

    async with aiohttp.ClientSession() as session:
        for i, forceid in enumerate(forceids, 1):
            print(f"\n[{i}/{len(forceids)}] 处理: {forceid}")

            # 查找审核结果文件
            result_file = None
            for claim_type in ["flight_delay", "baggage_delay", "baggage_damage"]:
                candidate = REVIEW_DIR / claim_type / f"{forceid}_ai_review.json"
                if candidate.exists():
                    result_file = candidate
                    break

            if not result_file:
                print(f"  未找到审核结果文件，跳过")
                continue

            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  读取审核结果失败: {e}")
                continue

            audit = result.get("flight_delay_audit") or result.get("DebugInfo", {}).get("flight_delay_audit") or {}
            print(f"  audit_result: {audit.get('audit_result', 'unknown')}")
            print(f"  Remark: {str(result.get('Remark', ''))[:80]}")

            # 1. 推送前端
            try:
                push_result = await push_to_frontend(result, session)
                if push_result.get("success"):
                    print(f"  [OK] 推送前端成功")
                else:
                    print(f"  [FAIL] 推送前端失败: {push_result.get('response', '')[:120]}")
            except Exception as e:
                print(f"  [ERR] 推送前端异常: {e}")

            # 2. 提取完整字段并写数据库
            try:
                claim_info = claim_info_cache.get(forceid, {})
                fields = workflow._extract_review_fields(result, claim_info)
                db_ok = _sync_to_db(fields)
                if db_ok:
                    scenario = fields.get("flight_scenario", "-")
                    avi_status = fields.get("avi_status", "-")
                    alt_fn = fields.get("alt_flight_no", "-")
                    delay_from = fields.get("delay_calc_from", "-")
                    delay_to = fields.get("delay_calc_to", "-")
                    print(f"  [OK] 数据库同步成功")
                    print(f"    flight_scenario={scenario}, avi_status={avi_status}, alt_flight_no={alt_fn}")
                    print(f"    delay_calc: {delay_from} -> {delay_to}")
                else:
                    print(f"  [FAIL] 数据库同步失败")
            except Exception as e:
                import traceback
                print(f"  [ERR] 数据库同步异常: {e}")
                traceback.print_exc()

    print("\n全部处理完成")


if __name__ == "__main__":
    forceids = sys.argv[1:]
    if not forceids:
        print("用法: python scripts/push_existing_results.py <forceid1> <forceid2> ...")
        sys.exit(1)
    asyncio.run(push_and_sync(forceids))
