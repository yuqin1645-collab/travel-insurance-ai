#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
挑样本重跑（无材料/模型失败重试）
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import aiohttp
import os
import json
import time
from typing import Dict, List, Optional, Tuple
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.config import config
from app.logging_utils import LOGGER, log_extra as _log_extra  # 复用同一套日志
from app.policy_terms_registry import POLICY_TERMS

MATERIAL_SUFFIXES = {'.jpg', '.jpeg', '.png', '.pdf', '.docx', '.doc'}


def _list_claim_folders() -> List[Path]:
    claims_dir = config.CLAIMS_DATA_DIR
    folders = []
    # 兼容 claims_data 平铺/命名空间两种结构：递归找 claim_info.json
    for info_file in claims_dir.rglob("claim_info.json"):
        folder = info_file.parent
        if folder.name.startswith("."):
            continue
        folders.append(folder)
    return sorted(set(folders), key=lambda p: str(p))


def _has_any_material_files(claim_folder: Path) -> bool:
    for f in claim_folder.iterdir():
        if f.is_file() and f.name != 'claim_info.json' and f.suffix.lower() in MATERIAL_SUFFIXES:
            return True
    return False


def _load_claim_info(claim_folder: Path) -> Dict:
    with open(claim_folder / "claim_info.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _build_forceid_to_folder_map(claim_folders: List[Path]) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    for folder in claim_folders:
        try:
            info = _load_claim_info(folder)
            forceid = info.get("forceid")
            if forceid:
                mapping[str(forceid)] = folder
        except Exception:
            continue
    return mapping


def _pick_empty_material_cases(claim_folders: List[Path], n: int) -> List[Path]:
    """保留原有函数以兼容老的批量抽样逻辑（目前交互模式默认不用）"""
    empties = [f for f in claim_folders if not _has_any_material_files(f)]
    return empties[: max(0, n)]


def _should_retry(result: Optional[Dict], error: Optional[Exception]) -> bool:
    if error is not None:
        return True
    if not result:
        return True
    remark = str(result.get("Remark") or "")
    # 仅"系统侧/调用侧"可重试：不要把业务态的"转人工审核/补件"当成重试条件
    retry_markers = ["系统异常", "材料审核系统异常", "超时", "timeout", "429", "503", "502"]
    should = any(m in remark for m in retry_markers)
    if should:
        # 找出匹配了哪个关键词
        matched = [m for m in retry_markers if m in remark]
        print(f"[DEBUG] Remark triggered retry: matched={matched}, remark[:100]={remark[:100]}")
    return should


def _detect_claim_type(claim_folder: Path) -> str:
    """从 claim_info.json 的 BenefitName 判断案件类型"""
    try:
        info = _load_claim_info(claim_folder)
        benefit = str(info.get("BenefitName") or "")
        if "航班延误" in benefit:
            return "flight_delay"
        return "baggage_damage"
    except Exception:
        return "baggage_damage"


def _remove_existing_result_file(claim_folder: Path) -> None:
    """
    为了让测试"重跑"，把历史结果文件移除，避免触发"已审核过"短路。
    """
    try:
        info = _load_claim_info(claim_folder)
        forceid = info.get("forceid")
        if not forceid:
            return
        # 兼容新老目录：优先删命名空间目录下的结果，其次删旧的平铺文件
        # claim_type 以 BenefitName/目录判断为主（避免航延写到随身财产目录）
        benefit = str(info.get("BenefitName") or "")
        claim_type = "flight_delay" if ("航班延误" in benefit or "航班延误" in str(claim_folder)) else "baggage_damage"
        ns_file = config.REVIEW_RESULTS_DIR / claim_type / f"{forceid}_ai_review.json"
        if ns_file.exists():
            ns_file.unlink()
        flat_file = config.REVIEW_RESULTS_DIR / f"{forceid}_ai_review.json"
        if flat_file.exists():
            flat_file.unlink()
    except Exception:
        return


async def run_cases(claim_folders: List[Path]):
    """给定案件目录列表，逐个跑 AI 审核（带自动重试）"""
    if not claim_folders:
        LOGGER.info("没有需要处理的案件。", extra=_log_extra())
        return

    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info("开始执行 AI 审核", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    
    # 设置代理
    http_proxy = os.getenv('HTTP_PROXY', 'http://127.0.0.1:7897')
    LOGGER.info(f"使用代理: {http_proxy}", extra=_log_extra(stage="runner"))
    
    reviewer = AIClaimReviewer()
    
    # 条款按案件类型动态读取（每个案件可能不同类型），此处先置空
    policy_terms_cache: Dict[str, str] = {}
    
    max_retries = int(os.getenv("TEST_MAX_RETRIES", "3"))
    retry_sleep = float(os.getenv("TEST_RETRY_SLEEP_SEC", "3"))
    
    LOGGER.info(f"找到 {len(claim_folders)} 个案件待审核:", extra=_log_extra(stage="runner"))
    for i, folder in enumerate(claim_folders, 1):
        tag = "无材料" if not _has_any_material_files(folder) else "有材料"
        LOGGER.info(f"  {i}. {folder.name} ({tag})", extra=_log_extra(stage="runner"))
    
    start_time = time.time()
    
    # 创建共享的aiohttp session
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(
        connector=connector,
        trust_env=True
    ) as session:
        # 逐个处理(不并发,方便调试)
        all_results = []
        
        for i, claim_folder in enumerate(claim_folders, 1):
            LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
            LOGGER.info(f"处理案件 {i}/{len(claim_folders)}: {claim_folder.name}", extra=_log_extra(stage="runner"))
            LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
            
            try:
                # 删除历史结果，确保真正重跑
                _remove_existing_result_file(claim_folder)

                # 按案件类型动态读取条款
                detected_type = _detect_claim_type(claim_folder)
                if detected_type not in policy_terms_cache:
                    try:
                        terms_file = POLICY_TERMS.resolve(detected_type)
                        with open(terms_file, 'r', encoding='utf-8') as f:
                            policy_terms_cache[detected_type] = f.read()
                    except Exception as e:
                        LOGGER.warning(f"条款文件读取失败({detected_type}): {e}", extra=_log_extra(stage="runner"))
                        policy_terms_cache[detected_type] = ""
                policy_terms = policy_terms_cache[detected_type]

                last_error: Optional[Exception] = None
                result: Optional[Dict] = None
                for attempt in range(1, max_retries + 1):
                    try:
                        result = await review_claim_async(
                            reviewer, claim_folder, policy_terms,
                            i, len(claim_folders), session
                        )
                        last_error = None
                    except Exception as e:
                        last_error = e
                        result = None

                    if not _should_retry(result, last_error):
                        break

                    if attempt < max_retries:
                        # 尽量输出"为什么重试"，便于定位
                        if last_error is not None:
                            reason = f"异常: {str(last_error)[:200]}"
                        else:
                            debug = (result or {}).get("DebugInfo") or {}
                            debug_tail = ""
                            try:
                                tail = (debug.get("debug") or [])[-1]
                                debug_tail = f"；DebugInfo: {tail}"
                            except Exception:
                                debug_tail = ""
                            reason = f"Remark命中可重试标记: {(result or {}).get('Remark')}{debug_tail}"
                        LOGGER.warning(
                            f"重试 {attempt}/{max_retries}（{reason}），等待 {retry_sleep} 秒...",
                            extra=_log_extra(stage="runner", attempt=attempt),
                        )
                        await asyncio.sleep(retry_sleep)
                
                if not result and last_error is not None:
                    # 兜底：把最终失败也落盘，避免"只看到整流程重试但不知道哪里错"
                    forceid = "unknown"
                    try:
                        info = _load_claim_info(claim_folder)
                        forceid = str(info.get("forceid") or "unknown")
                    except Exception:
                        pass
                    result = {
                        "forceid": forceid,
                        "Remark": f"系统异常: {str(last_error)[:200]}",
                        "IsAdditional": "Y",
                        "KeyConclusions": [
                            {
                                "checkpoint": "系统处理",
                                "Eligible": "N",
                                "Remark": f"处理异常，需要人工审核: {str(last_error)[:200]}"
                            }
                        ],
                        "DebugInfo": {"debug": [{"stage": "outer", "attempt": max_retries, "error": str(last_error)[:200]}]},
                    }

                if result:
                    all_results.append(result)
                    
                    # 保存单个案件结果
                    claim_type = str(result.get("claim_type") or result.get("claimType") or detected_type)
                    output_dir = config.REVIEW_RESULTS_DIR / claim_type
                    output_dir.mkdir(parents=True, exist_ok=True)
                    
                    result_file = output_dir / f"{result['forceid']}_ai_review.json"
                    with open(result_file, 'w', encoding='utf-8') as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    
                    LOGGER.info(f"结果已保存: {result_file}", extra=_log_extra(forceid=str(result.get("forceid") or "-"), stage="runner"))
                    
            except Exception as e:
                LOGGER.error(f"案件处理失败: {e}", extra=_log_extra(stage="runner"))
                import traceback
                traceback.print_exc()
    
    # 生成API标准返回格式
    api_response = {
        "msg": None,
        "code": 200,
        "data": all_results
    }
    
    # 保存汇总结果
    summary_file = (config.REVIEW_RESULTS_DIR / "_runner") / "test_last_5_results.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(api_response, f, ensure_ascii=False, indent=2)
    
    # 计算耗时
    elapsed_time = time.time() - start_time
    
    # 打印审核统计
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info("测试完成!", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info(f"总案件数: {len(claim_folders)}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"成功: {len(all_results)}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"失败: {len(claim_folders) - len(all_results)}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"总耗时: {elapsed_time:.1f}秒", extra=_log_extra(stage="runner"))
    LOGGER.info(f"平均速度: {elapsed_time/len(claim_folders):.1f}秒/案件", extra=_log_extra(stage="runner"))
    LOGGER.info(f"汇总结果已保存: {summary_file}", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))


def main() -> None:
    all_claim_folders = _list_claim_folders()
    forceid_map = _build_forceid_to_folder_map(all_claim_folders)

    # 非交互模式：
    # - python test_last_5.py <forceid_or_range_or_csv>
    #   例如：python test_last_5.py a0n... 或 a0n1~a0n9 或 a0n1,a0n2
    if len(sys.argv) > 1:
        # 支持 --force-ids a,b,c 或直接 a,b,c
        raw_args = sys.argv[1:]
        if "--force-ids" in raw_args:
            idx = raw_args.index("--force-ids")
            user_input = raw_args[idx + 1] if idx + 1 < len(raw_args) else ""
        else:
            user_input = " ".join(raw_args).strip()
        claim_folders: List[Path] = []
        if "~" in user_input:
            start, end = [x.strip() for x in user_input.split("~", 1)]
            keys = sorted(forceid_map.keys())
            selected = [k for k in keys if start <= k <= end]
            claim_folders = [forceid_map[k] for k in selected]
        elif "," in user_input or "，" in user_input or " " in user_input:
            import re as _re
            parts = [p.strip() for p in _re.split(r"[，,\s]+", user_input) if p.strip()]
            for fid in parts:
                folder = forceid_map.get(fid)
                if folder and folder not in claim_folders:
                    claim_folders.append(folder)
        else:
            folder = forceid_map.get(user_input)
            if folder:
                claim_folders = [folder]

        if not claim_folders:
            LOGGER.warning("未找到对应案件，请检查 forceid 是否正确。", extra=_log_extra(stage="runner"))
            return
        LOGGER.info(f"非交互模式：本次将测试 {len(claim_folders)} 个案件", extra=_log_extra(stage="runner"))
        asyncio.run(run_cases(claim_folders))
        return

    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info("交互式测试指定案件（按 forceid）", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info("输入格式示例:", extra=_log_extra(stage="runner"))
    LOGGER.info("  单个: a0nC800000IehQTIAZ", extra=_log_extra(stage="runner"))
    LOGGER.info("  多个: a0nC800000IehQTIAZ,a0nC800000I93W4IAJ", extra=_log_extra(stage="runner"))
    LOGGER.info("  区间: a0nC800000I93W4IAJ~a0nC800000IehQTIAZ  (按 forceid 排序的闭区间)", extra=_log_extra(stage="runner"))
    LOGGER.info("输入 q 或直接回车退出。", extra=_log_extra(stage="runner"))

    while True:
        try:
            text = input("请输入 forceid / 区间: ").strip()
        except (EOFError, KeyboardInterrupt):
            LOGGER.info("再见", extra=_log_extra(stage="runner"))
            break

        if not text or text.lower() == "q":
            LOGGER.info("再见", extra=_log_extra(stage="runner"))
            break

        # 解析输入
        claim_folders: List[Path] = []
        user_input = text

        # 区间: start~end
        if "~" in user_input:
            start, end = [x.strip() for x in user_input.split("~", 1)]
            keys = sorted(forceid_map.keys())
            selected = [k for k in keys if start <= k <= end]
            claim_folders = [forceid_map[k] for k in selected]
        # 多个: 逗号分隔
        elif "," in user_input or "，" in user_input:
            import re as _re

            parts = [p.strip() for p in _re.split(r"[，,]", user_input) if p.strip()]
            for fid in parts:
                folder = forceid_map.get(fid)
                if folder and folder not in claim_folders:
                    claim_folders.append(folder)
        # 单个
        else:
            folder = forceid_map.get(user_input)
            if folder:
                claim_folders = [folder]

        if not claim_folders:
            LOGGER.warning("未找到对应案件，请检查 forceid 是否正确。", extra=_log_extra(stage="runner"))
            continue

        LOGGER.info(f"本次将测试 {len(claim_folders)} 个案件:", extra=_log_extra(stage="runner"))
        for f in claim_folders:
            LOGGER.info(f"  - {f.name}", extra=_log_extra(stage="runner"))

        asyncio.run(run_cases(claim_folders))


if __name__ == "__main__":
    main()
