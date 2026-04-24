#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一推送脚本：推送前端 / 同步数据库 / 批量推送

用法:
  python push.py --forceid xxx                    # 推送指定 forceid 到前端+数据库
  python push.py --all --type baggage             # 批量推送所有行李延误
  python push.py --all --type flight              # 批量推送所有航班延误
  python push.py --sync-db                        # 全量同步数据库（所有险种）
  python push.py --sync-db --dry-run              # 预览同步，不写入
"""

import sys
import json
import asyncio
import os
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

import aiohttp
import pymysql
from app.config import config
from app.output.frontend_pusher import push_to_frontend
from app.production.main_workflow import ProductionWorkflow

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


# ─────────────────────────────────────────────
# 子命令：推送单个 forceid
# ─────────────────────────────────────────────

async def cmd_push_forceid(forceids: list):
    workflow = ProductionWorkflow()
    claim_info_cache = _build_claim_info_cache()

    async with aiohttp.ClientSession() as session:
        for fid in forceids:
            result_file = next(REVIEW_DIR.rglob(f"{fid}_ai_review.json"), None)
            if not result_file:
                print(f"  未找到审核结果: {fid}")
                continue

            result = json.loads(result_file.read_text(encoding="utf-8"))
            remark = str(result.get("Remark", ""))[:80]
            print(f"  处理: {fid} | {remark}")

            # 推送前端
            try:
                push_result = await push_to_frontend(result, session)
                print(f"  前端: {'成功' if push_result.get('success') else '失败'}")
            except Exception as e:
                print(f"  前端异常: {e}")

            # 写数据库
            claim_info = claim_info_cache.get(fid, {})
            fields = workflow._extract_review_fields(result, claim_info)
            db_ok = _sync_to_db(fields)
            print(f"  数据库: {'成功' if db_ok else '失败'}")


# ─────────────────────────────────────────────
# 子命令：批量推送（按险种）
# ─────────────────────────────────────────────

async def cmd_push_all(claim_type: str):
    type_dir_map = {"baggage": "baggage_delay", "flight": "flight_delay"}
    baggage_dir = REVIEW_DIR / type_dir_map.get(claim_type, "baggage_delay")
    if not baggage_dir.exists():
        print(f"目录不存在: {baggage_dir}")
        return

    forceids = sorted([
        fn.replace("_ai_review.json", "")
        for fn in os.listdir(baggage_dir)
        if fn.endswith("_ai_review.json")
    ])
    print(f"找到 {len(forceids)} 个审核结果")

    workflow = ProductionWorkflow()
    claim_info_cache = _build_claim_info_cache()

    ok_frontend = ok_db = 0
    async with aiohttp.ClientSession() as session:
        for i, fid in enumerate(forceids, 1):
            result_file = baggage_dir / f"{fid}_ai_review.json"
            try:
                result = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            # 推送前端
            try:
                push_result = await push_to_frontend(result, session)
                if push_result.get("success"):
                    ok_frontend += 1
            except Exception:
                pass

            # 写数据库
            try:
                claim_info = claim_info_cache.get(fid, {})
                fields = workflow._extract_review_fields(result, claim_info)
                if _sync_to_db(fields):
                    ok_db += 1
            except Exception:
                pass

            if i % 50 == 0:
                print(f"  进度: {i}/{len(forceids)}")

    print(f"\n前端成功: {ok_frontend}/{len(forceids)}")
    print(f"数据库成功: {ok_db}/{len(forceids)}")


# ─────────────────────────────────────────────
# 子命令：全量同步数据库
# ─────────────────────────────────────────────

def cmd_sync_db(dry_run: bool = False):
    results = []
    for f in REVIEW_DIR.rglob("*_ai_review.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("forceid"):
                results.append(data)
        except Exception:
            pass

    print(f"找到 {len(results)} 条审核结果")
    if dry_run:
        for r in results[:5]:
            print(f"  示例: forceid={r['forceid']} remark={str(r.get('Remark', ''))[:60]}")
        print(f"(dry-run 模式，共 {len(results)} 条)")
        return

    import pymysql
    conn = pymysql.connect(
        host=os.getenv("DB_HOST", ""),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
    )
    success = fail = 0
    try:
        with conn.cursor() as cur:
            sql = """
                INSERT INTO ai_review_result (forceid, claim_id, remark, is_additional, key_conclusions, raw_result)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    claim_id = VALUES(claim_id),
                    remark = VALUES(remark),
                    is_additional = VALUES(is_additional),
                    key_conclusions = VALUES(key_conclusions),
                    raw_result = VALUES(raw_result),
                    updated_at = CURRENT_TIMESTAMP
            """
            for r in results:
                try:
                    fid = r.get("forceid", "")
                    remark = r.get("Remark", "")[:2000]
                    is_additional = str(r.get("IsAdditional", "Y"))[:1]
                    key_conclusions = json.dumps(r.get("KeyConclusions", []), ensure_ascii=False)
                    raw_result = json.dumps(r, ensure_ascii=False)
                    cur.execute(sql, (fid, "", remark, is_additional, key_conclusions, raw_result))
                    success += 1
                except Exception as e:
                    fail += 1
                    print(f"  写入失败 {r.get('forceid')}: {e}")
        conn.commit()
    finally:
        conn.close()

    print(f"成功: {success}, 失败: {fail}")


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="统一推送脚本")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--forceid", nargs="+", help="推送指定 forceid")
    group.add_argument("--all", action="store_true", help="批量推送所有")
    group.add_argument("--sync-db", action="store_true", help="全量同步数据库")
    parser.add_argument("--type", choices=["baggage", "flight"], default="baggage", help="险种（--all 时有效）")
    parser.add_argument("--dry-run", action="store_true", help="预览不写入（--sync-db 时有效）")
    args = parser.parse_args()

    if args.forceid:
        asyncio.run(cmd_push_forceid(args.forceid))
    elif args.all:
        asyncio.run(cmd_push_all(args.type))
    elif args.sync_db:
        cmd_sync_db(args.dry_run)


if __name__ == "__main__":
    main()
