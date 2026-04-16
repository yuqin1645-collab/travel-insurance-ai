
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于 .download_progress.json 恢复历史案件到本地并执行全链路处理。

默认流程：
1) 读取进度文件（默认仓库根目录 .download_progress.json）
2) 按事故月份筛选（默认 2026-02）
3) 下载/补齐附件到 claims_data
4) 执行 AI 审核
5) 推送前端
6) 同步数据库
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
import requests
from dotenv import load_dotenv

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.config import config
from app.output.frontend_pusher import push_to_frontend
from app.policy_terms_registry import POLICY_TERMS
from scripts.download_claims import detect_extension
from scripts.sync_review_to_db import sync_review_to_db_for_forceid


CONCLUDED_STATUSES = {
    "零结关案",
    "支付成功",
    "事后理赔拒赔",
    "取消理赔",
    "结案待财务付款",
}


def _safe_text(v: Any) -> str:
    return str(v or "").strip()


def _to_case_no(entry_key: str, record: Dict[str, Any]) -> str:
    return _safe_text(
        record.get("caseNo")
        or record.get("CaseNo")
        or record.get("claimNo")
        or record.get("ClaimNo")
        or record.get("policyNo")
        or record.get("PolicyNo")
        or entry_key
    )


def _to_forceid(record: Dict[str, Any]) -> str:
    return _safe_text(record.get("forceid") or record.get("ForceID") or record.get("Id"))


def _to_benefit_name(record: Dict[str, Any]) -> str:
    return _safe_text(record.get("benefitName") or record.get("BenefitName"))


def _to_claim_type(record: Dict[str, Any]) -> str:
    benefit = _to_benefit_name(record)
    if "航班延误" in benefit:
        return "flight_delay"
    return "baggage_damage"


def _get_case_dir(case_no: str, benefit_name: str) -> Path:
    return config.CLAIMS_DATA_DIR / benefit_name / f"{benefit_name}-案件号【{case_no}】"


def _is_month_match(record: Dict[str, Any], month: str) -> bool:
    accident_date = _safe_text(record.get("date_of_Accident") or record.get("dateOfAccident"))
    return accident_date.startswith(month)


def _normalize_file_list(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    files = (
        record.get("fileList")
        or record.get("FileList")
        or record.get("files")
        or record.get("Files")
        or record.get("attachments")
        or record.get("Attachments")
        or []
    )
    if isinstance(files, list):
        return [f for f in files if isinstance(f, dict)]
    return []


def _file_id(file_info: Dict[str, Any]) -> str:
    explicit = _safe_text(
        file_info.get("FileId")
        or file_info.get("fileId")
        or file_info.get("Id")
        or file_info.get("id")
    )
    if explicit:
        return explicit

    url = _file_url(file_info)
    if url:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if path:
            return path.split("/")[-1]
    return ""


def _file_url(file_info: Dict[str, Any]) -> str:
    return _safe_text(
        file_info.get("Url")
        or file_info.get("url")
        or file_info.get("FileUrl")
        or file_info.get("fileUrl")
        or file_info.get("FileURL")
        or file_info.get("fileURL")
        or file_info.get("DownloadUrl")
        or file_info.get("downloadUrl")
    )


def _file_name(file_info: Dict[str, Any]) -> str:
    name = _safe_text(
        file_info.get("FileName")
        or file_info.get("fileName")
        or file_info.get("Name")
        or file_info.get("name")
    )
    if name:
        return name

    url = _file_url(file_info)
    if not url:
        return ""

    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if path:
        fname = path.split("/")[-1]
        if fname and "." in fname:
            return unquote(fname)

    params = parse_qs(parsed.query)
    if "filebridge.alipay.com" in parsed.netloc:
        file_keys = params.get("fileKey", [])
        if file_keys and file_keys[0]:
            fk = file_keys[0].rstrip("/")
            return unquote(fk.split("/")[-1])

    for key in ("filename", "name", "file"):
        vals = params.get(key, [])
        if vals and vals[0]:
            return unquote(vals[0])
    return ""


def _find_existing_file_by_stem(folder: Path, stem: str) -> Optional[Path]:
    if not folder.exists():
        return None
    for f in folder.iterdir():
        if f.is_file() and f.name != "claim_info.json" and f.stem == stem:
            return f
    return None


def _save_claim_info(case_dir: Path, record: Dict[str, Any], case_no: str) -> None:
    info = dict(record)
    if "fileList" not in info:
        info["fileList"] = _normalize_file_list(record)
    info.setdefault("CaseNo", case_no)
    info.setdefault("caseNo", case_no)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "claim_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _download_one_file(url: str, dest: Path, retries: int, timeout: int) -> Tuple[bool, str]:
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            data = resp.content
            actual = dest
            if not actual.suffix:
                actual = actual.with_suffix(detect_extension(data))
            actual.parent.mkdir(parents=True, exist_ok=True)
            actual.write_bytes(data)
            return True, actual.name
        except Exception as e:
            if attempt < retries:
                time.sleep(min(2 * attempt, 5))
            else:
                return False, str(e)
    return False, "unknown"

def _download_case_materials(
    case_no: str,
    case_dir: Path,
    record: Dict[str, Any],
    retries: int,
    timeout: int,
) -> Tuple[int, int, List[str]]:
    files = _normalize_file_list(record)
    if not files:
        return 0, 0, []

    downloaded = 0
    failed = 0
    failed_items: List[str] = []

    for file_info in files:
        fid = _file_id(file_info)
        furl = _file_url(file_info)
        fname = _file_name(file_info) or fid

        if not fid:
            fid = Path(fname).stem if fname else ""
        if not fid or not furl:
            failed += 1
            failed_items.append(f"{case_no}:missing_file_id_or_url")
            continue

        if _find_existing_file_by_stem(case_dir, Path(fid).stem):
            downloaded += 1
            continue

        dest_name = fname or fid
        ok, detail = _download_one_file(furl, case_dir / dest_name, retries, timeout)
        if ok:
            downloaded += 1
        else:
            failed += 1
            failed_items.append(f"{case_no}:{fid}:{detail[:120]}")

    return downloaded, failed, failed_items


def _select_cases(
    progress_data: Dict[str, Any],
    month: str,
    include_concluded: bool,
    forceids: Optional[set[str]],
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for key, raw in progress_data.items():
        if not isinstance(raw, dict):
            continue
        case_no = _to_case_no(key, raw)
        forceid = _to_forceid(raw)
        benefit_name = _to_benefit_name(raw)
        if not (case_no and forceid and benefit_name):
            continue
        if not _is_month_match(raw, month):
            continue
        if forceids and forceid not in forceids:
            continue
        final_status = _safe_text(raw.get("final_Status") or raw.get("Final_Status") or raw.get("final_status"))
        if (not include_concluded) and final_status in CONCLUDED_STATUSES:
            continue

        out.append(
            {
                "key": key,
                "case_no": case_no,
                "forceid": forceid,
                "benefit_name": benefit_name,
                "claim_type": _to_claim_type(raw),
                "record": raw,
            }
        )
        if limit and len(out) >= limit:
            break
    return out


def _has_material_files(case_dir: Path) -> bool:
    if not case_dir.exists():
        return False
    for f in case_dir.iterdir():
        if f.is_file() and f.name != "claim_info.json":
            return True
    return False


def _save_local_progress(selected_cases: List[Dict[str, Any]], source_path: Path) -> Path:
    target = config.CLAIMS_DATA_DIR / ".download_progress.json"
    target.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Any] = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    for c in selected_cases:
        existing[c["case_no"]] = c["record"]

    existing["_meta_recovered_from"] = str(source_path)
    existing["_meta_recovered_at"] = datetime.now().isoformat()
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


async def _review_push_sync(
    selected_cases: List[Dict[str, Any]],
    dry_run: bool,
) -> Dict[str, int]:
    reviewer = AIClaimReviewer()
    policy_terms_cache: Dict[str, str] = {}
    summary = {"review_ok": 0, "review_fail": 0, "push_fail": 0, "db_fail": 0}

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(),
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=180),
    ) as session:
        for idx, item in enumerate(selected_cases, 1):
            forceid = item["forceid"]
            claim_type = item["claim_type"]
            case_dir = _get_case_dir(item["case_no"], item["benefit_name"])

            if not _has_material_files(case_dir):
                print(f"[SKIP] {idx}/{len(selected_cases)} {forceid} 无材料文件")
                summary["review_fail"] += 1
                continue

            if claim_type not in policy_terms_cache:
                try:
                    terms_file = POLICY_TERMS.resolve(claim_type)
                    policy_terms_cache[claim_type] = terms_file.read_text(encoding="utf-8")
                except Exception as e:
                    print(f"[WARN] 条款加载失败 {claim_type}: {e}")
                    policy_terms_cache[claim_type] = ""

            review_result = None
            for attempt in range(1, 4):
                try:
                    review_result = await review_claim_async(
                        reviewer,
                        case_dir,
                        policy_terms_cache[claim_type],
                        idx,
                        len(selected_cases),
                        session,
                    )
                    break
                except Exception as e:
                    if attempt < 3:
                        print(f"[WARN] {forceid} 审核失败 attempt {attempt}/3: {e}，重试中")
                        await asyncio.sleep(3)
                    else:
                        print(f"[FAIL] {forceid} 审核失败: {e}")

            if not review_result:
                summary["review_fail"] += 1
                continue

            out_dir = config.REVIEW_RESULTS_DIR / claim_type
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{forceid}_ai_review.json"
            out_file.write_text(json.dumps(review_result, ensure_ascii=False, indent=2), encoding="utf-8")
            summary["review_ok"] += 1

            if dry_run:
                print(f"[DRY] {forceid} 审核完成，跳过推送和DB同步")
                continue

            push_ok = False
            try:
                push_ret = await push_to_frontend(review_result, session)
                push_ok = bool(push_ret.get("success"))
            except Exception as e:
                print(f"[FAIL] {forceid} 前端推送异常: {e}")
            if not push_ok:
                summary["push_fail"] += 1

            db_ok = False
            try:
                db_ok = sync_review_to_db_for_forceid(review_result)
            except Exception as e:
                print(f"[FAIL] {forceid} DB同步异常: {e}")
            if not db_ok:
                summary["db_fail"] += 1

            print(
                f"[OK] {idx}/{len(selected_cases)} {forceid} "
                f"review=ok push={'ok' if push_ok else 'fail'} db={'ok' if db_ok else 'fail'}"
            )
    return summary

async def main_async(args: argparse.Namespace) -> int:
    source_path = Path(args.progress_file).expanduser().resolve()
    if not source_path.exists():
        print(f"[ERROR] 进度文件不存在: {source_path}")
        return 2

    data = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        print(f"[ERROR] 进度文件格式不正确: {source_path}")
        return 2

    forceids = set(args.forceids) if args.forceids else None
    selected = _select_cases(
        progress_data=data,
        month=args.month,
        include_concluded=args.include_concluded,
        forceids=forceids,
        limit=args.limit,
    )
    if not selected:
        print(f"[DONE] 未找到满足条件的案件 month={args.month}")
        return 0

    print(f"[INFO] 命中案件: {len(selected)} (month={args.month})")
    local_progress_path = _save_local_progress(selected, source_path)
    print(f"[INFO] 已同步筛选结果到: {local_progress_path}")

    download_ok = 0
    download_fail = 0
    fail_items: List[str] = []

    if not args.review_only:
        print("[STEP] 开始下载/补齐附件")
        for idx, item in enumerate(selected, 1):
            case_no = item["case_no"]
            benefit_name = item["benefit_name"]
            case_dir = _get_case_dir(case_no, benefit_name)
            _save_claim_info(case_dir, item["record"], case_no)

            got, failed, errors = _download_case_materials(
                case_no=case_no,
                case_dir=case_dir,
                record=item["record"],
                retries=args.download_retries,
                timeout=args.download_timeout,
            )
            if failed == 0 and got > 0:
                download_ok += 1
            elif failed == 0 and got == 0 and _has_material_files(case_dir):
                download_ok += 1
            else:
                download_fail += 1
                fail_items.extend(errors)
            print(
                f"[DL] {idx}/{len(selected)} case={case_no} forceid={item['forceid']} "
                f"success={got} failed={failed}"
            )
    else:
        print("[STEP] --review-only 模式，跳过下载")

    if args.download_only:
        print(
            f"[DONE] 仅下载完成: success_cases={download_ok}, failed_cases={download_fail}, total={len(selected)}"
        )
        if fail_items:
            print("[FAIL-DETAIL] 下载失败样例:")
            for line in fail_items[:20]:
                print(f"  - {line}")
        return 0 if download_fail == 0 else 1

    print("[STEP] 开始审核 + 前端推送 + DB同步")
    summary = await _review_push_sync(selected, dry_run=args.dry_run)

    print("=" * 70)
    print(
        f"[SUMMARY] month={args.month} total={len(selected)} "
        f"download_ok={download_ok} download_fail={download_fail} "
        f"review_ok={summary['review_ok']} review_fail={summary['review_fail']} "
        f"push_fail={summary['push_fail']} db_fail={summary['db_fail']}"
    )
    if fail_items:
        print("[FAIL-DETAIL] 下载失败样例:")
        for line in fail_items[:20]:
            print(f"  - {line}")
    print("=" * 70)

    if summary["review_fail"] > 0 or summary["push_fail"] > 0 or summary["db_fail"] > 0:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按月份从 .download_progress.json 恢复历史案件并执行本地全链路"
    )
    parser.add_argument(
        "--progress-file",
        default=str(ROOT / ".download_progress.json"),
        help="来源进度文件路径（默认仓库根目录 .download_progress.json）",
    )
    parser.add_argument(
        "--month",
        default="2026-02",
        help="事故月份，格式 YYYY-MM（默认 2026-02）",
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少条案件")
    parser.add_argument(
        "--forceids",
        nargs="*",
        default=None,
        help="仅处理指定 forceid 列表（可选）",
    )
    parser.add_argument(
        "--include-concluded",
        action="store_true",
        help="包含已结案状态（默认不包含）",
    )
    parser.add_argument("--download-only", action="store_true", help="只下载，不审核")
    parser.add_argument("--review-only", action="store_true", help="只审核，不下载")
    parser.add_argument("--dry-run", action="store_true", help="审核后不推送前端，不同步DB")
    parser.add_argument("--download-retries", type=int, default=3, help="单文件下载重试次数")
    parser.add_argument("--download-timeout", type=int, default=120, help="单文件下载超时秒数")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.download_only and args.review_only:
        print("[ERROR] --download-only 与 --review-only 不能同时使用")
        raise SystemExit(2)
    code = asyncio.run(main_async(args))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
