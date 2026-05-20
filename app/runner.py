#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI理赔审核系统 — 批量运行与单案件编排。

从 app/claim_ai_reviewer.py 提取，独立为 runner 模块。
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.claim_ai_reviewer import AIClaimReviewer
from app.config import config
from app.logging_utils import LOGGER, log_extra as _log_extra
from app.modules.baggage_damage.pipeline import review_baggage_damage_async
from app.modules.baggage_delay.pipeline import review_baggage_delay_async
from app.modules.flight_delay.pipeline import review_flight_delay_async
from app.modules.router import detect_claim_type
from app.policy_terms_registry import POLICY_TERMS


# ══════════════════════════════════════════════════════════════════════════════
# 单案件编排
# ══════════════════════════════════════════════════════════════════════════════

async def review_claim_async(
    reviewer: AIClaimReviewer,
    claim_folder: Path,
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession,
) -> Optional[Dict[str, Any]]:
    """异步审核单个案件。"""
    forceid: str = "unknown"
    ctx: Dict[str, Any] = {
        "coverage": None,
        "materials": None,
        "accident": None,
        "compensation": None,
        "debug": [],
    }

    stage_max_retries = int(os.getenv("STAGE_MAX_RETRIES", os.getenv("MAIN_MAX_RETRIES", "3")))
    stage_retry_sleep = float(os.getenv("STAGE_RETRY_SLEEP_SEC", os.getenv("MAIN_RETRY_SLEEP_SEC", "3")))

    try:
        claim_info = reviewer._load_claim_info(claim_folder)
        claim_info["_claim_folder_path"] = str(claim_folder)
        forceid = claim_info.get("forceid", "unknown")

        benefit = str(claim_info.get("BenefitName") or "")
        claim_type = detect_claim_type(benefit=benefit, folder_hint=str(claim_folder))
        reviewer.set_claim_type(claim_type)
        claim_info["claim_type"] = claim_type

        LOGGER.info(
            f"[{index}/{total}] 开始审核 {claim_folder.name}",
            extra=_log_extra(forceid=forceid, stage="start", attempt=0),
        )

        if claim_type == "flight_delay":
            return await review_flight_delay_async(
                reviewer=reviewer,
                claim_folder=claim_folder,
                claim_info=claim_info,
                policy_terms=policy_terms or "",
                index=index,
                total=total,
                session=session,
            )
        if claim_type == "baggage_delay":
            return await review_baggage_delay_async(
                reviewer=reviewer,
                claim_folder=claim_folder,
                claim_info=claim_info,
                policy_terms=policy_terms or "",
                index=index,
                total=total,
                session=session,
            )

        return await review_baggage_damage_async(
            reviewer=reviewer,
            claim_folder=claim_folder,
            claim_info=claim_info,
            policy_terms=policy_terms or "",
            index=index,
            total=total,
            session=session,
            stage_max_retries=stage_max_retries,
            stage_retry_sleep=stage_retry_sleep,
            ctx=ctx,
        )

    except Exception as e:
        LOGGER.error(
            f"[{index}/{total}] 异常: {e}",
            extra=_log_extra(forceid=forceid, stage="exception", attempt=0),
            exc_info=True,
        )

        return {
            "forceid": forceid,
            "claim_type": claim_info.get("claim_type", "baggage_damage") if "claim_info" in locals() else "baggage_damage",
            "Remark": f"系统异常，转人工处理: {str(e)}",
            "IsAdditional": "Y",
            "KeyConclusions": [
                {
                    "checkpoint": "系统异常",
                    "Eligible": "需人工判断",
                    "Remark": str(e),
                }
            ],
            "DebugInfo": ctx,
        }


# ══════════════════════════════════════════════════════════════════════════════
# 批量运行入口
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """同步审核系统(兼容旧版)"""
    asyncio.run(main_async())


async def main_async():
    """异步并发审核系统 - 大幅提升处理速度"""

    # 验证配置
    if not config.validate():
        LOGGER.error("配置验证失败,请检查.env文件", extra=_log_extra(stage="runner"))
        return

    reviewer = AIClaimReviewer()

    # 读取保险条款（当前强制随身财产）
    policy_terms_file = POLICY_TERMS.resolve("baggage_damage")
    with open(policy_terms_file, "r", encoding="utf-8") as f:
        policy_terms = f.read()

    # 审核案件目录枚举：
    # - 兼容平铺：claims_data/<case_folder>/claim_info.json
    # - 兼容命名空间：claims_data/<claim_type>/<case_folder>/claim_info.json
    claims_dir = config.CLAIMS_DATA_DIR
    claim_folders = []
    for info_file in claims_dir.rglob("claim_info.json"):
        folder = info_file.parent
        if folder.name.startswith("."):
            continue
        claim_folders.append(folder)
    # 去重&排序（避免重复处理/保证日志稳定）
    claim_folders = sorted(set(claim_folders), key=lambda p: str(p))

    # 可选：只跑某个案件类型
    only_claim_type = (os.getenv("ONLY_CLAIM_TYPE") or "").strip()
    if only_claim_type:
        only_claim_type = only_claim_type.lower()
        if only_claim_type not in {"flight_delay", "baggage_delay", "baggage_damage"}:
            LOGGER.warning(
                f"ONLY_CLAIM_TYPE={only_claim_type} 不支持，将忽略（仅支持 flight_delay/baggage_delay/baggage_damage）",
                extra=_log_extra(stage="runner"),
            )
        else:
            filtered = []
            for folder in claim_folders:
                try:
                    with open(folder / "claim_info.json", "r", encoding="utf-8") as f:
                        info = json.load(f)
                    benefit = str(info.get("BenefitName") or "")
                except Exception:
                    benefit = ""
                detected = detect_claim_type(benefit=benefit, folder_hint=str(folder))
                if only_claim_type == detected:
                    filtered.append(folder)
                elif only_claim_type == "baggage_damage" and detected == "baggage_damage":
                    filtered.append(folder)
            claim_folders = filtered

    # 可选：只跑指定 forceid
    only_forceid = (os.getenv("ONLY_FORCEID") or "").strip()
    if only_forceid:
        mapping = {}
        for folder in claim_folders:
            try:
                with open(folder / "claim_info.json", "r", encoding="utf-8") as f:
                    info = json.load(f)
                fid = str(info.get("forceid") or "").strip()
                if fid:
                    mapping[fid] = folder
            except Exception:
                continue

        selected_folders = []
        if "~" in only_forceid:
            start, end = [x.strip() for x in only_forceid.split("~", 1)]
            keys = sorted(mapping.keys())
            for k in keys:
                if start <= k <= end:
                    selected_folders.append(mapping[k])
        elif "," in only_forceid or "，" in only_forceid:
            parts = [p.strip() for p in re.split(r"[，,]", only_forceid) if p.strip()]
            for fid in parts:
                folder = mapping.get(fid)
                if folder and folder not in selected_folders:
                    selected_folders.append(folder)
        else:
            folder = mapping.get(only_forceid)
            if folder:
                selected_folders = [folder]

        if not selected_folders:
            LOGGER.warning(
                f"ONLY_FORCEID={only_forceid} 未匹配到案件，将退出",
                extra=_log_extra(stage="runner"),
            )
            return
        claim_folders = selected_folders

    total_claims = len(claim_folders)
    LOGGER.info(f"找到 {total_claims} 个案件待审核", extra=_log_extra(stage="runner"))
    LOGGER.info("使用异步并发处理 (每批最多10个案件同时处理)", extra=_log_extra(stage="runner"))

    start_time = time.time()

    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(
        connector=connector,
        trust_env=True,
    ) as session:
        main_max_retries = int(os.getenv("MAIN_MAX_RETRIES", "3"))
        main_retry_sleep = float(os.getenv("MAIN_RETRY_SLEEP_SEC", "3"))

        async def _run_with_retry(claim_folder: Path, idx: int) -> Optional[Dict[str, Any]]:
            last_exc: Optional[Exception] = None
            for attempt in range(1, max(1, main_max_retries) + 1):
                try:
                    return await review_claim_async(
                        reviewer, claim_folder, policy_terms,
                        idx, total_claims, session,
                    )
                except Exception as e:
                    last_exc = e
                    if attempt < main_max_retries:
                        err_lower = str(e).lower()
                        is_conn = any(kw in err_lower for kw in ("cannot connect", "connection", "ssl", "timeout", "network"))
                        wait = main_retry_sleep * (2 ** (attempt - 1)) * (3 if is_conn else 1)
                        wait = min(wait, 120.0)
                        LOGGER.warning(
                            f"[{idx}/{total_claims}] 调用异常（{'连接错误' if is_conn else '一般错误'}），"
                            f"第 {attempt} 次失败，{wait:.1f}s 后重试...",
                            extra=_log_extra(stage="runner", attempt=attempt),
                        )
                        await asyncio.sleep(wait)
            if last_exc:
                raise last_exc
            return None

        batch_size = 3
        all_results = []

        for batch_start in range(0, total_claims, batch_size):
            batch_end = min(batch_start + batch_size, total_claims)
            batch_folders = claim_folders[batch_start:batch_end]

            LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
            LOGGER.info(f"处理批次: {batch_start+1}-{batch_end}/{total_claims}", extra=_log_extra(stage="runner"))
            LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))

            tasks = []
            for i, claim_folder in enumerate(batch_folders, batch_start + 1):
                task = _run_with_retry(claim_folder, i)
                tasks.append(task)

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results.extend(batch_results)

        results = all_results

    # 处理结果
    all_results = []
    success_count = 0
    failed_count = 0

    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            failed_count += 1
            LOGGER.error(f"[失败] 案件 {i} 审核失败: {result}", extra=_log_extra(stage="runner"))
        elif result is not None:
            all_results.append(result)
            success_count += 1

            claim_type = str(result.get("claim_type") or result.get("claimType") or "baggage_damage")
            output_dir = config.REVIEW_RESULTS_DIR / claim_type
            output_dir.mkdir(parents=True, exist_ok=True)
            result_file = output_dir / f"{result['forceid']}_ai_review.json"
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

    # 汇总结果
    api_response = {
        "msg": None,
        "code": 200,
        "data": all_results,
    }
    summary_file = (config.REVIEW_RESULTS_DIR / "_runner") / "api_response.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(api_response, f, ensure_ascii=False, indent=2)

    elapsed_time = time.time() - start_time

    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info("批量审核完成!", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info(f"总案件数: {total_claims}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"成功: {success_count}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"失败: {failed_count}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"成功率: {success_count/total_claims*100:.1f}%", extra=_log_extra(stage="runner"))
    LOGGER.info(f"总耗时: {elapsed_time:.1f}秒", extra=_log_extra(stage="runner"))
    LOGGER.info(f"平均速度: {elapsed_time/total_claims:.1f}秒/案件", extra=_log_extra(stage="runner"))
    LOGGER.info(f"API响应已保存: {summary_file}", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))


if __name__ == "__main__":
    main()