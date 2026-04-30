#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一审核脚本：航班延误 / 行李延误 / 指定 forceid / 重新审核

用法:
  python review.py                        # 默认：行李延误+航班延误全量审核
  python review.py --type baggage         # 只跑行李延误
  python review.py --type flight          # 只跑航班延误
  python review.py --forceid xxx xxx2     # 重跑指定 forceid
  python review.py --redownloaded         # 扫描重新下载的案件并重审
  python review.py --analyze              # 统计分析审核结果分布
  python review.py --analyze --top 30     # 统计 Top 30
"""

import sys
import os
import json
import asyncio
import argparse
import re
import pymysql
from pathlib import Path
from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple
from datetime import datetime

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.config import config
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.policy_terms_registry import POLICY_TERMS
from app.output.frontend_pusher import push_to_frontend
from app.state.constants import ClaimStatus
from app.state.status_manager import get_status_manager
from app.logging_utils import LOGGER, log_extra as _log_extra

import aiohttp

CLAIMS_DIR = config.CLAIMS_DATA_DIR
REVIEW_DIR = config.REVIEW_RESULTS_DIR
MATERIAL_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".doc"}
API_URL = os.getenv("CLAIMS_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim")

CONCLUDED_STATUSES = {
    "零结关案", "支付成功", "事后理赔拒赔", "取消理赔", "结案待财务付款"
}


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def detect_claim_type(claim_info: Dict) -> str:
    benefit = str(claim_info.get("BenefitName") or "")
    if "航班延误" in benefit:
        return "flight_delay"
    if "行李延误" in benefit:
        return "baggage_delay"
    return "baggage_damage"


def find_claim_folder(forceid: str) -> Path | None:
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                return info_file.parent
        except Exception:
            continue
    return None


def get_reviewed_forceids() -> set:
    reviewed = set()
    for f in REVIEW_DIR.rglob("*_ai_review.json"):
        reviewed.add(f.name.replace("_ai_review.json", ""))
    return reviewed


def has_material_files(claim_folder: Path) -> bool:
    for f in claim_folder.iterdir():
        if f.is_file() and f.name != "claim_info.json" and f.suffix.lower() in MATERIAL_SUFFIXES:
            return True
    return False


async def _review_single(
    claim_folder: Path,
    policy_terms: str,
    session: aiohttp.ClientSession,
    idx: int = 1,
    total: int = 1,
) -> Dict | None:
    """跑单个案件审核，返回审核结果 dict"""
    info = json.loads((claim_folder / "claim_info.json").read_text(encoding="utf-8"))
    forceid = str(info.get("forceid") or "").strip()
    claim_type = detect_claim_type(info)

    reviewer = AIClaimReviewer()
    for attempt in range(1, 4):
        try:
            result = await review_claim_async(
                reviewer, claim_folder, policy_terms, idx, total, session
            )
            return result
        except Exception as e:
            LOGGER.warning(
                f"审核失败 attempt={attempt}: {e}",
                extra=_log_extra(forceid=forceid, stage="review"),
            )
            if attempt < 3:
                await asyncio.sleep(3)
    return None


async def _save_and_push(result: Dict, session: aiohttp.ClientSession):
    """保存审核结果文件 + 推送前端 + 同步数据库 + 注册 claim_status"""
    forceid = str(result.get("forceid") or "")
    claim_type = str(result.get("claim_type") or "baggage_delay")

    # 保存文件
    output_dir = REVIEW_DIR / claim_type
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = output_dir / f"{forceid}_ai_review.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 推送前端
    push_result = await push_to_frontend(result, session)
    if push_result.get("success"):
        LOGGER.info(f"推送前端成功: {forceid}", extra=_log_extra(forceid=forceid, stage="review"))
    else:
        LOGGER.error(f"推送前端失败: {forceid}", extra=_log_extra(forceid=forceid, stage="review"))

    # 同步数据库（主表 + 子表）
    try:
        from app.production.main_workflow import ProductionWorkflow
        workflow = ProductionWorkflow()
        claim_info = {}
        folder = find_claim_folder(forceid)
        if folder:
            claim_info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
        main_fields, flight_fields, baggage_fields = workflow._extract_review_fields(result, claim_info)
        conn = pymysql.connect(
            host=os.getenv("DB_HOST", ""), port=int(os.getenv("DB_PORT", "3306")),
            user=os.getenv("DB_USER", ""), password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "ai"), charset="utf8mb4",
        )
        try:
            with conn.cursor() as cur:
                # 1. 写主表
                keys = list(main_fields.keys())
                placeholders = ", ".join(["%s"] * len(keys))
                update_clause = ", ".join([f"{k}=VALUES({k})" for k in keys if k != "forceid"])
                sql = (
                    f"INSERT INTO ai_review_result ({', '.join(keys)}) "
                    f"VALUES ({placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {update_clause}, updated_at=CURRENT_TIMESTAMP"
                )
                cur.execute(sql, list(main_fields.values()))

                # 2. 写子表
                ct = main_fields.get("claim_type", "")
                if ct == "flight_delay" and flight_fields:
                    fkeys = list(flight_fields.keys())
                    fph = ", ".join(["%s"] * len(fkeys))
                    fup = ", ".join([f"{k}=VALUES({k})" for k in fkeys if k != "forceid"])
                    fsql = (
                        f"INSERT INTO ai_flight_delay_data ({', '.join(fkeys)}) "
                        f"VALUES ({fph}) ON DUPLICATE KEY UPDATE {fup}"
                    )
                    cur.execute(fsql, list(flight_fields.values()))
                elif ct == "baggage_delay" and baggage_fields:
                    bkeys = list(baggage_fields.keys())
                    bph = ", ".join(["%s"] * len(bkeys))
                    bup = ", ".join([f"{k}=VALUES({k})" for k in bkeys if k != "forceid"])
                    bsql = (
                        f"INSERT INTO ai_baggage_delay_data ({', '.join(bkeys)}) "
                        f"VALUES ({bph}) ON DUPLICATE KEY UPDATE {bup}"
                    )
                    cur.execute(bsql, list(baggage_fields.values()))
            conn.commit()
            LOGGER.info(f"数据库同步成功: {forceid}", extra=_log_extra(forceid=forceid, stage="review"))
        finally:
            conn.close()
    except Exception as e:
        LOGGER.warning(f"数据库同步失败: {forceid}: {e}", extra=_log_extra(forceid=forceid, stage="review"))

    # 注册 claim_status
    try:
        status_mgr = get_status_manager()
        existing = await status_mgr.get_claim_status(forceid)
        if existing is None:
            folder = find_claim_folder(forceid)
            if folder:
                info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
                claim_id = info.get("ClaimId") or info.get("caseNo") or forceid
                await status_mgr.create_claim_status(
                    claim_id=claim_id, forceid=forceid,
                    claim_type=claim_type, initial_status=ClaimStatus.DOWNLOADED,
                )
    except Exception:
        pass


# ─────────────────────────────────────────────
# 清理：关闭 aiomysql 连接池
# ─────────────────────────────────────────────

async def _cleanup_db_pool():
    """关闭 aiomysql 连接池，避免 Python GC 在 event loop 关闭后清理时报错"""
    try:
        from app.db.database import get_db_connection
        await get_db_connection().close()
    except Exception:
        pass


# ─────────────────────────────────────────────
# 子命令：批量审核
# ─────────────────────────────────────────────

async def cmd_review(claim_type: str | None):
    """批量审核（全量或指定险种）"""
    types = []
    if claim_type == "baggage":
        types = ["baggage_delay"]
    elif claim_type == "flight":
        types = ["flight_delay"]
    else:
        types = ["flight_delay", "baggage_delay"]

    reviewed = get_reviewed_forceids()
    policy_terms_cache: Dict[str, str] = {}

    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        all_folders = []
        for info_file in CLAIMS_DIR.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
                forceid = str(data.get("forceid") or "").strip()
                final_status = str(data.get("Final_Status") or "").strip()
                if forceid in reviewed or final_status in CONCLUDED_STATUSES:
                    continue
                if not has_material_files(info_file.parent):
                    continue
                ct = detect_claim_type(data)
                if ct in types:
                    all_folders.append(info_file.parent)
            except Exception:
                continue

        if not all_folders:
            print("没有需要审核的新案件")
            return

        for ct in types:
            if ct not in policy_terms_cache:
                try:
                    terms_file = POLICY_TERMS.resolve(ct)
                    policy_terms_cache[ct] = terms_file.read_text(encoding="utf-8")
                except Exception:
                    policy_terms_cache[ct] = ""

        print(f"共 {len(all_folders)} 个案件待审核")
        for i, claim_folder in enumerate(all_folders, 1):
            ct = detect_claim_type(json.loads((claim_folder / "claim_info.json").read_text(encoding="utf-8")))
            print(f"  [{i}/{len(all_folders)}] {claim_folder.name} ({ct})")
            result = await _review_single(claim_folder, policy_terms_cache[ct], session, i, len(all_folders))
            if result:
                await _save_and_push(result, session)

    print(f"\n审核完成")
    await _cleanup_db_pool()


# ─────────────────────────────────────────────
# 子命令：重跑指定 forceid
# ─────────────────────────────────────────────

async def cmd_rerun(forceids: List[str]):
    """重跑指定 forceid"""
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        for i, fid in enumerate(forceids, 1):
            folder = find_claim_folder(fid)
            if not folder:
                print(f"  未找到案件目录: {fid}，尝试下载...")
                try:
                    from scripts.download_claims import ClaimDownloader
                    from scripts.find_claim_by_forceid import fetch_by_forceid
                    claim_data = fetch_by_forceid(fid)
                    downloader = ClaimDownloader(
                        api_url=API_URL, output_dir=str(CLAIMS_DIR), force_refresh=True,
                    )
                    downloader.process_claim(claim_data)
                    folder = find_claim_folder(fid)
                except Exception as e:
                    print(f"  下载失败: {e}")
                    continue
            else:
                # 目录已存在但可能缺少附件文件，用本地 claim_info.json 中的 FileList 补下载
                if not has_material_files(folder):
                    print(f"  目录存在但无附件: {fid}，尝试补下载...")
                    try:
                        from scripts.download_claims import ClaimDownloader
                        info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
                        downloader = ClaimDownloader(
                            api_url=API_URL, output_dir=str(CLAIMS_DIR), force_refresh=True,
                        )
                        downloader.process_claim(info)
                        folder = find_claim_folder(fid)
                    except Exception as e:
                        print(f"  补下载失败: {e}")
                        # 本地 FileList 下载失败，再尝试 API 查询
                        try:
                            from scripts.find_claim_by_forceid import fetch_by_forceid
                            claim_data = fetch_by_forceid(fid)
                            downloader = ClaimDownloader(
                                api_url=API_URL, output_dir=str(CLAIMS_DIR), force_refresh=True,
                            )
                            downloader.process_claim(claim_data)
                            folder = find_claim_folder(fid)
                        except Exception as e2:
                            print(f"  API补下载也失败: {e2}")
                            continue

            if not folder:
                print(f"  跳过: {fid}（未找到目录）")
                continue

            info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
            ct = detect_claim_type(info)
            try:
                terms_file = POLICY_TERMS.resolve(ct)
                policy_terms = terms_file.read_text(encoding="utf-8")
            except Exception:
                policy_terms = ""

            print(f"  [{i}/{len(forceids)}] 审核: {folder.name} ({ct})")
            result = await _review_single(folder, policy_terms, session, i, len(forceids))
            if result:
                await _save_and_push(result, session)

    print(f"\n重跑完成")
    await _cleanup_db_pool()


# ─────────────────────────────────────────────
# 子命令：重新审核重新下载的案件
# ─────────────────────────────────────────────

async def cmd_rerun_redownloaded():
    """扫描 claims_data 中已有目录但未审核的案件"""
    reviewed = get_reviewed_forceids()
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        policy_terms_cache: Dict[str, str] = {}
        folders = []
        for info_file in CLAIMS_DIR.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
                fid = str(data.get("forceid") or "").strip()
                fs = str(data.get("Final_Status") or "").strip()
                if fid and fid not in reviewed and fs not in CONCLUDED_STATUSES and has_material_files(info_file.parent):
                    folders.append(info_file.parent)
            except Exception:
                continue

        if not folders:
            print("没有需要重新审核的案件")
            return

        for folder in folders:
            info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
            ct = detect_claim_type(info)
            if ct not in policy_terms_cache:
                try:
                    policy_terms_cache[ct] = POLICY_TERMS.resolve(ct).read_text(encoding="utf-8")
                except Exception:
                    policy_terms_cache[ct] = ""

            print(f"  审核: {folder.name} ({ct})")
            result = await _review_single(folder, policy_terms_cache[ct], session)
            if result:
                await _save_and_push(result, session)

    print(f"\n重审完成")
    await _cleanup_db_pool()


# ─────────────────────────────────────────────
# 子命令：统计分析
# ─────────────────────────────────────────────

def cmd_analyze(top_n: int = 15):
    """统计分析审核结果分布"""

    def _category(item: Dict) -> str:
        remark = str(item.get("Remark") or "")
        is_add = str(item.get("IsAdditional") or "")
        if is_add.upper() == "Y":
            if "Vision模式失败" in remark or "材料审核系统异常" in remark:
                return "补件/人工:Vision或系统异常"
            if "需要人工审核" in remark:
                return "补件/人工:需要人工审核"
            if "需要补充材料" in remark:
                return "补件/人工:缺件"
            return "补件/人工:其他"
        if "拒赔" in remark:
            return "拒赔"
        if "赔付" in remark or "通过" in remark:
            return "通过/赔付"
        return "其他"

    def _reject_bucket(remark: str) -> str:
        buckets: List[Tuple[str, List[str]]] = [
            ("保额/限额", ["超过剩余保额", "超过保额", "限额"]),
            ("保单有效期", ["不在保单有效期内", "有效期"]),
            ("出行时间早于生效", ["最早出行日期", "保单自始无效", "出境后才投保"]),
            ("除外责任", ["除外", "免责", "触发除外责任"]),
            ("不属保障范围", ["不属于保障范围", "不符合保障责任"]),
            ("赔付金额为0", ["赔偿金额为0", "无可赔付金额", "金额为0"]),
        ]
        for name, keys in buckets:
            if any(k in remark for k in keys):
                return name
        return "其他拒赔原因"

    items = []
    for f in REVIEW_DIR.rglob("*_ai_review.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("forceid"):
                items.append(data)
        except Exception:
            continue

    if not items:
        print("未找到任何审核结果")
        return

    total = len(items)
    cat_counter = Counter()
    reject_bucket = Counter()
    examples: Dict[str, List[str]] = defaultdict(list)

    for it in items:
        cat = _category(it)
        cat_counter[cat] += 1
        remark = str(it.get("Remark") or "")
        if cat == "拒赔":
            rb = _reject_bucket(remark)
            reject_bucket[rb] += 1
            if len(examples.get(f"拒赔:{rb}", [])) < top_n:
                examples.setdefault(f"拒赔:{rb}", []).append(it["forceid"])
        else:
            if len(examples.get(cat, [])) < top_n:
                examples.setdefault(cat, []).append(it["forceid"])

    print("=" * 60)
    print("审核结果统计")
    print("=" * 60)
    print(f"总数: {total}")
    print()
    print("分布:")
    for k, v in cat_counter.most_common():
        print(f"  - {k}: {v} ({v/total:.1%})")
    print()
    if cat_counter.get("拒赔"):
        print("拒赔原因分桶:")
        for k, v in reject_bucket.most_common():
            print(f"  - {k}: {v} ({v/cat_counter['拒赔']:.1%})")
        print()
    print(f"典型 forceid（每类最多 {top_n} 个）:")
    for k in sorted(examples.keys()):
        print(f"  - {k}: {', '.join(examples[k])}")
    print("=" * 60)


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="统一审核脚本")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--type", choices=["flight", "baggage"], help="指定险种")
    group.add_argument("--forceid", nargs="+", help="重跑指定 forceid")
    group.add_argument("--redownloaded", action="store_true", help="重审未审核案件")
    group.add_argument("--analyze", action="store_true", help="统计分析")
    parser.add_argument("--top", type=int, default=15, help="analyze 模式 TopN")
    args = parser.parse_args()

    if args.analyze:
        cmd_analyze(args.top)
    elif args.forceid:
        asyncio.run(cmd_rerun(args.forceid))
    elif args.redownloaded:
        asyncio.run(cmd_rerun_redownloaded())
    else:
        asyncio.run(cmd_review(args.type))


if __name__ == "__main__":
    main()
