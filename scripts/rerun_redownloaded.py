#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重新审核因材料缺失/不全而重新下载的案件

完整流程：重新下载 → AI重审 → 推送前端 → 同步数据库

用法:
  venv\\Scripts\\python.exe scripts\\rerun_redownloaded.py              # 自动扫描并重审
  venv\\Scripts\\python.exe scripts\\rerun_redownloaded.py --dry-run   # 仅预览，不执行
  venv\\Scripts\\python.exe scripts\\rerun_redownloaded.py a0nC... b0nD...  # 指定 forceid 重审
"""

import argparse
import asyncio
import json
import os
import sys
import aiohttp
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from app.config import config
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.policy_terms_registry import POLICY_TERMS
from app.output.frontend_pusher import push_to_frontend


# ─────────────────────────────────────────────
# 步骤1：扫描需要重新审核的案件
# ─────────────────────────────────────────────

def scan_redownloaded_cases() -> list[dict]:
    """
    扫描需要重新审核的案件：
    - 自愈机制重置过的案件（磁盘无材料但进度记已完成 → 重新下载了）
    - force_refresh 强制重下的案件

    返回 [{forceid, case_no, benefit_name, claim_folder, reason}]
    """
    progress_file = config.CLAIMS_DATA_DIR / ".download_progress.json"
    if not progress_file.exists():
        print(f"[警告] 进度文件不存在: {progress_file}")
        return []

    with open(progress_file, "r", encoding="utf-8") as f:
        progress = json.load(f)

    results = []

    for case_no, record in progress.items():
        status = record.get("status", "")
        total_files = record.get("totalFiles", 0)
        downloaded_files = record.get("downloadedFiles", [])
        benefit_name = record.get("benefitName", "")

        # 只处理「已有材料」的案件（totalFiles > 0 且 downloadedFiles 非空）
        if total_files == 0 or len(downloaded_files) == 0:
            continue

        # 跳过结案状态
        final_status = str(record.get("final_Status") or record.get("final_status") or "").strip()
        CONCLUDED = {"零结关案", "支付成功", "事后理赔拒赔", "取消理赔", "结案待财务付款"}
        if final_status in CONCLUDED:
            continue

        # 通过 forceid 找案件目录
        forceid = record.get("forceid") or record.get("Id") or ""
        if not forceid:
            # 从 claim_info.json 反查
            case_dir = config.CLAIMS_DATA_DIR / benefit_name / f"{benefit_name}-案件号【{case_no}】"
            info_path = case_dir / "claim_info.json"
            if info_path.exists():
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                    forceid = str(info.get("forceid") or info.get("ForceID") or "").strip()
                except Exception:
                    pass

        if not forceid:
            continue

        claim_folder = config.CLAIMS_DATA_DIR / benefit_name / f"{benefit_name}-案件号【{case_no}】"

        # 判断是否需要重审：审核文件不存在 或 比下载完成时间更旧
        completed_time = record.get("completedTime", "")
        review_file_path = _find_review_file(forceid)

        need_rerun = False
        reason = ""

        if not review_file_path or not review_file_path.exists():
            need_rerun = True
            reason = "无审核结果文件"
        elif completed_time:
            try:
                completed_dt = datetime.fromisoformat(completed_time)
                review_mtime = datetime.fromtimestamp(review_file_path.stat().st_mtime)
                if review_mtime < completed_dt:
                    need_rerun = True
                    reason = f"审核结果旧于下载完成时间（审核: {review_mtime.strftime('%m-%d %H:%M')}, 下载: {completed_dt.strftime('%m-%d %H:%M')}）"
            except Exception:
                # 无法比较时间，安全起见重审
                need_rerun = True
                reason = "无法比较时间（重审）"
        else:
            # 没有下载完成时间，无法判断新旧，但有材料没审核 → 重审
            need_rerun = True
            reason = "无法判断新旧，有材料待审"

        if need_rerun:
            results.append({
                "forceid": forceid,
                "case_no": case_no,
                "benefit_name": benefit_name,
                "claim_folder": claim_folder,
                "reason": reason,
            })

    return results


def _find_review_file(forceid: str) -> Path | None:
    """查找审核结果文件路径（flight_delay / baggage_damage 两种）"""
    for subdir in ("flight_delay", "baggage_damage"):
        path = config.REVIEW_RESULTS_DIR / subdir / f"{forceid}_ai_review.json"
        if path.exists():
            return path
    return None


def detect_claim_type(claim_folder: Path) -> str:
    """从 claim_info.json 判断案件类型"""
    info_path = claim_folder / "claim_info.json"
    if not info_path.exists():
        return "baggage_damage"
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
        benefit = str(data.get("BenefitName") or "")
        if "延误" in benefit:
            return "flight_delay"
    except Exception:
        pass
    return "baggage_damage"


# ─────────────────────────────────────────────
# 步骤2：执行 AI 重审 + 推送 + 同步数据库
# ─────────────────────────────────────────────

async def rerun_single_case(
    reviewer: AIClaimReviewer,
    policy_terms_cache: dict,
    session: aiohttp.ClientSession,
    case_info: dict,
    dry_run: bool,
) -> dict:
    """处理单个案件的重审流程"""
    forceid = case_info["forceid"]
    claim_folder = case_info["claim_folder"]
    reason = case_info["reason"]

    result_out = {
        "forceid": forceid,
        "case_no": case_info["case_no"],
        "benefit_name": case_info["benefit_name"],
        "action": "skip",
        "message": "",
    }

    # 验证目录存在
    if not claim_folder.exists():
        result_out["message"] = f"案件目录不存在: {claim_folder}"
        return result_out

    # 验证有材料文件
    has_material = any(
        f.is_file() and f.name != "claim_info.json"
        for f in claim_folder.iterdir()
    )
    if not has_material:
        result_out["message"] = "无材料文件，跳过"
        return result_out

    if dry_run:
        result_out["action"] = "dry-run"
        result_out["message"] = f"[dry-run] {reason}，会执行：AI审核 → 推送前端 → 同步DB"
        return result_out

    claim_type = detect_claim_type(claim_folder)

    # 加载条款
    if claim_type not in policy_terms_cache:
        try:
            terms_file = POLICY_TERMS.resolve(claim_type)
            policy_terms_cache[claim_type] = terms_file.read_text(encoding="utf-8")
        except Exception as e:
            policy_terms_cache[claim_type] = ""

    # ── AI 审核 ──
    review_result = None
    for attempt in range(1, 4):
        try:
            review_result = await review_claim_async(
                reviewer, claim_folder, policy_terms_cache[claim_type],
                0, 0, session
            )
            break
        except Exception as e:
            if attempt < 3:
                await asyncio.sleep(3)
            else:
                result_out["message"] = f"AI审核失败（3次）: {e}"
                return result_out

    if not review_result:
        result_out["message"] = "AI审核无结果"
        return result_out

    # ── 保存审核结果（覆盖旧文件）──
    output_dir = config.REVIEW_RESULTS_DIR / claim_type
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = output_dir / f"{forceid}_ai_review.json"
    result_file.write_text(
        json.dumps(review_result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # ── 推送前端（覆盖旧结论）──
    push_ok = False
    try:
        push_result = await push_to_frontend(review_result, session)
        push_ok = push_result.get("success", False)
        if not push_ok:
            result_out["message"] += f" | 推送前端失败: {push_result.get('response', '')[:80]}"
    except Exception as e:
        result_out["message"] = f"推送前端异常: {e}"

    # ── 同步数据库 ──
    try:
        from scripts.sync_review_to_db import sync_review_to_db_for_forceid
        db_ok = sync_review_to_db_for_forceid(review_result)
        if not db_ok:
            result_out["message"] += " | 数据库同步失败"
    except Exception as e:
        result_out["message"] = f"数据库同步异常: {e}"

    audit_summary = review_result.get("flight_delay_audit", {}).get("audit_result", "未知")
    result_out["action"] = "ok"
    result_out["message"] = f"审核: {audit_summary} | 推送: {'成功' if push_ok else '失败'} | {reason}"

    return result_out


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

async def run(dry_run: bool, forceids: list[str]):
    reviewer = AIClaimReviewer()
    policy_terms_cache = {}
    http_proxy = os.getenv("HTTP_PROXY", "http://127.0.0.1:7897")

    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(
        connector=connector,
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=120)
    ) as session:

        if forceids:
            # ── 指定 forceid 模式 ──
            cases = []
            for forceid in forceids:
                folder = _find_case_folder_by_forceid(forceid)
                if not folder:
                    print(f"[跳过] 未找到 forceid={forceid} 的案件目录")
                    continue
                cases.append({
                    "forceid": forceid,
                    "case_no": "",
                    "benefit_name": folder.parent.name,
                    "claim_folder": folder,
                    "reason": "用户指定重审",
                })
        else:
            # ── 自动扫描模式 ──
            print("=" * 60)
            print("步骤1：扫描需要重新审核的案件（因材料重新下载）")
            print("=" * 60)
            cases = scan_redownloaded_cases()

        if not cases:
            print("没有需要重新审核的案件")
            return

        print(f"\n共 {len(cases)} 个案件需要重审：")
        for c in cases:
            print(f"  [{c['forceid']}] {c['case_no']} - {c['reason']}")

        print(f"\n{'=' * 60}")
        print("步骤2：AI重审 → 推送前端 → 同步数据库")
        print(f"{'=' * 60}\n")

        ok_count = 0
        skip_count = 0

        for i, case_info in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}] {case_info['forceid']} | {case_info['reason']}")

            result = await rerun_single_case(
                reviewer, policy_terms_cache, session, case_info, dry_run
            )

            icon = "✓" if result["action"] == "ok" else ("⚠" if result["action"] == "dry-run" else "✗")
            print(f"  {icon} {result['message']}\n")

            if result["action"] == "ok":
                ok_count += 1
            elif result["action"] == "skip":
                skip_count += 1

        print("=" * 60)
        print(f"完成：成功 {ok_count}，跳过 {skip_count}，总计 {len(cases)}")
        print("=" * 60)


def _find_case_folder_by_forceid(forceid: str) -> Path | None:
    """根据 forceid 找到案件目录"""
    for info_file in config.CLAIMS_DATA_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or data.get("ForceID") or "").strip() == forceid:
                return info_file.parent
        except Exception:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(
        description="重新审核因材料缺失而重新下载的案件（下载→审核→推送→同步DB）"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行实际操作")
    parser.add_argument("forceids", nargs="*", help="指定要重审的 forceid（可选）")
    args = parser.parse_args()

    asyncio.run(run(dry_run=args.dry_run, forceids=args.forceids))


if __name__ == "__main__":
    main()
