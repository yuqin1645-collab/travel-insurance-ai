#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量联调脚本：拉取增量案件 → 跳过已有案件 → AI审核 → 回传前端

用法:
  python run_incremental.py                  # 正常运行
  python run_incremental.py --dry-run        # 只下载+审核，不推送前端
  python run_incremental.py --no-download    # 跳过下载，只对 claims_data 中未审核的案件跑审核+推送
"""

import sys
import os
import json
import asyncio
import aiohttp
import argparse
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from scripts.download_claims import ClaimDownloader
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.config import config
from app.logging_utils import LOGGER, log_extra as _log_extra
from app.policy_terms_registry import POLICY_TERMS
from app.output.frontend_pusher import push_to_frontend

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
API_URL = os.getenv("CLAIMS_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim")
CLAIMS_DIR = config.CLAIMS_DATA_DIR
REVIEW_DIR = config.REVIEW_RESULTS_DIR
MATERIAL_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".doc"}


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def get_existing_case_nos() -> set:
    """扫描 claims_data 中已有的 CaseNo（进度文件 + 实际目录两处兼顾）"""
    existing = set()
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            case_no = str(data.get("CaseNo") or data.get("caseNo") or "").strip()
            if case_no:
                existing.add(case_no)
        except Exception:
            continue
    return existing


def get_reviewed_forceids() -> set:
    """已生成审核结果的 forceid 集合"""
    reviewed = set()
    for f in REVIEW_DIR.rglob("*_ai_review.json"):
        forceid = f.name.replace("_ai_review.json", "")
        reviewed.add(forceid)
    return reviewed


def detect_claim_type(claim_info: Dict) -> str:
    benefit = str(claim_info.get("BenefitName") or "")
    if "航班延误" in benefit:
        return "flight_delay"
    return "baggage_damage"


def has_material_files(claim_folder: Path) -> bool:
    for f in claim_folder.iterdir():
        if f.is_file() and f.name != "claim_info.json" and f.suffix.lower() in MATERIAL_SUFFIXES:
            return True
    return False


def find_claim_folder(forceid: str) -> Optional[Path]:
    """根据 forceid 在 claims_data 中找到案件目录"""
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                return info_file.parent
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

async def run(dry_run: bool = False, no_download: bool = False):
    LOGGER.info("=" * 60, extra=_log_extra(stage="incremental"))
    LOGGER.info("增量联调开始", extra=_log_extra(stage="incremental"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="incremental"))

    new_claim_folders: List[Path] = []

    # ── 步骤1：下载增量案件 ──
    if not no_download:
        LOGGER.info("[1/3] 拉取增量案件...", extra=_log_extra(stage="incremental"))

        existing_case_nos = get_existing_case_nos()
        LOGGER.info(f"  本地已有 {len(existing_case_nos)} 个案件", extra=_log_extra(stage="incremental"))

        downloader = ClaimDownloader(api_url=API_URL, output_dir=str(CLAIMS_DIR))
        claims = downloader.fetch_claims({})

        if not claims:
            LOGGER.info("  接口未返回任何案件", extra=_log_extra(stage="incremental"))
        else:
            # 已结案状态（优先级最高，直接跳过）
            CONCLUDED_STATUSES = {
                "零结关案",
                "支付成功",
                "事后理赔拒赔",
                "取消理赔",
                "结案待财务付款"
            }

            incremental = [
                c for c in claims
                if str(c.get("CaseNo") or c.get("caseNo") or "").strip() not in existing_case_nos
                and str(c.get("Final_Status") or "").strip() not in CONCLUDED_STATUSES
            ]

            concluded_count = sum(
                1 for c in claims
                if str(c.get("Final_Status") or "").strip() in CONCLUDED_STATUSES
            )

            LOGGER.info(
                f"  接口返回 {len(claims)} 条，已结案 {concluded_count} 条，新增待审核 {len(incremental)} 条",
                extra=_log_extra(stage="incremental"),
            )

            for claim in incremental:
                case_no = str(claim.get("CaseNo") or claim.get("caseNo") or "").strip()
                LOGGER.info(f"  下载新案件: {case_no}", extra=_log_extra(stage="incremental"))
                downloader.process_claim(claim)

                # 找到刚下载好的案件目录
                forceid = str(claim.get("forceid") or claim.get("ForceID") or "").strip()
                if forceid:
                    folder = find_claim_folder(forceid)
                    if folder:
                        new_claim_folders.append(folder)
    else:
        LOGGER.info("[1/3] 跳过下载（--no-download）", extra=_log_extra(stage="incremental"))

    # ── 步骤2：找出未审核的案件（新下载 + 已有但未审核）──
    LOGGER.info("[2/3] 确定待审核案件...", extra=_log_extra(stage="incremental"))

    if no_download:
        # 全量扫描 claims_data，找未审核的
        reviewed_ids = get_reviewed_forceids()
        CONCLUDED_STATUSES = {
            "零结关案",
            "支付成功",
            "事后理赔拒赔",
            "取消理赔",
            "结案待财务付款"
        }
        for info_file in CLAIMS_DIR.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
                forceid = str(data.get("forceid") or "").strip()
                final_status = str(data.get("Final_Status") or "").strip()
                if forceid and forceid not in reviewed_ids and final_status not in CONCLUDED_STATUSES:
                    new_claim_folders.append(info_file.parent)
            except Exception:
                continue

    if not new_claim_folders:
        LOGGER.info("  没有需要审核的新案件，流程结束", extra=_log_extra(stage="incremental"))
        return

    LOGGER.info(f"  共 {len(new_claim_folders)} 个案件待审核", extra=_log_extra(stage="incremental"))

    # ── 步骤3：AI审核 + 回传 ──
    LOGGER.info("[3/3] AI审核 + 回传前端...", extra=_log_extra(stage="incremental"))

    reviewer = AIClaimReviewer()
    policy_terms_cache: Dict[str, str] = {}
    http_proxy = os.getenv("HTTP_PROXY", "http://127.0.0.1:7897")

    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        for i, claim_folder in enumerate(new_claim_folders, 1):
            LOGGER.info(
                f"  [{i}/{len(new_claim_folders)}] 审核: {claim_folder.name}",
                extra=_log_extra(stage="incremental"),
            )

            try:
                # 读取案件类型并加载条款
                info = json.loads((claim_folder / "claim_info.json").read_text(encoding="utf-8"))
                forceid = str(info.get("forceid") or "").strip()
                claim_type = detect_claim_type(info)

                if claim_type not in policy_terms_cache:
                    try:
                        terms_file = POLICY_TERMS.resolve(claim_type)
                        policy_terms_cache[claim_type] = terms_file.read_text(encoding="utf-8")
                    except Exception as e:
                        LOGGER.warning(f"条款文件读取失败: {e}", extra=_log_extra(stage="incremental"))
                        policy_terms_cache[claim_type] = ""

                # AI审核（最多重试3次）
                result = None
                for attempt in range(1, 4):
                    try:
                        result = await review_claim_async(
                            reviewer, claim_folder, policy_terms_cache[claim_type],
                            i, len(new_claim_folders), session
                        )
                        break
                    except Exception as e:
                        LOGGER.warning(
                            f"审核失败 attempt={attempt}: {e}",
                            extra=_log_extra(forceid=forceid, stage="incremental"),
                        )
                        if attempt < 3:
                            await asyncio.sleep(3)

                if not result:
                    LOGGER.error(f"审核彻底失败: {forceid}", extra=_log_extra(stage="incremental"))
                    continue

                # 保存审核结果到文件
                output_dir = REVIEW_DIR / claim_type
                output_dir.mkdir(parents=True, exist_ok=True)
                result_file = output_dir / f"{result['forceid']}_ai_review.json"
                result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                LOGGER.info(f"  审核结果已保存: {result_file.name}", extra=_log_extra(forceid=forceid, stage="incremental"))

                # 回传前端
                if dry_run:
                    LOGGER.info(f"  [dry-run] 跳过推送: {forceid}", extra=_log_extra(stage="incremental"))
                else:
                    push_result = await push_to_frontend(result, session)
                    if push_result.get("success"):
                        LOGGER.info(f"  推送成功: {forceid}", extra=_log_extra(forceid=forceid, stage="incremental"))
                    else:
                        LOGGER.error(
                            f"  推送失败: {forceid} | {push_result.get('response', '')[:100]}",
                            extra=_log_extra(forceid=forceid, stage="incremental"),
                        )

            except Exception as e:
                LOGGER.error(f"  处理异常: {claim_folder.name}: {e}", extra=_log_extra(stage="incremental"))
                import traceback
                traceback.print_exc()

    LOGGER.info("=" * 60, extra=_log_extra(stage="incremental"))
    LOGGER.info("增量联调完成", extra=_log_extra(stage="incremental"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="incremental"))


def main():
    parser = argparse.ArgumentParser(description="增量联调：拉取 → 审核 → 回传")
    parser.add_argument("--dry-run", action="store_true", help="不实际推送前端，只跑审核")
    parser.add_argument("--no-download", action="store_true", help="跳过下载，只审核+推送未审核案件")
    args = parser.parse_args()

    asyncio.run(run(dry_run=args.dry_run, no_download=args.no_download))


if __name__ == "__main__":
    main()
