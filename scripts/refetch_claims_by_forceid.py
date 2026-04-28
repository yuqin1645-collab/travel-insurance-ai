#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 forceid 从 Rest_AI_CLaim 接口补拉指定案件的 claim_info.json

用法:
  python scripts/refetch_claims_by_forceid.py a0nC800000HTZv3IAH     # 补拉指定单个forceid
  python scripts/refetch_claims_by_forceid.py a001 a002 a003           # 补拉多个
  python scripts/refetch_claims_by_forceid.py --batch dup_forces.txt   # 从文件批量读取
  python scripts/refetch_claims_by_forceid.py --duplicates             # 补拉29条已知重复案件

说明:
  接口支持传 forceid 精确查询单条案件，无需分页。
  每次调用都会覆盖本地已有的 claim_info.json。
"""

import sys
import os
import json
import logging
import requests
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.config import config

API_URL = os.getenv("REST_AI_CLAIM_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim")

# 29条重复案件的 forceid
DUPLICATE_FORCEIDS = [
    'a0nC800000HTZv3IAH', 'a0nC800000I66N0IAJ', 'a0nC800000I6iUBIAZ',
    'a0nC800000I4ozxIAB', 'a0nC800000I7LyDIAV', 'a0nC800000JL58KIAT',
    'a0nC800000JFlJrIAL', 'a0nC800000IMlQyIAL', 'a0nC800000IUIraIAH',
    'a0nC800000IatznIAB', 'a0nC800000IatzpIAB', 'a0nC800000J8hmSIAR',
    'a0nC800000Ig7Z1IAJ', 'a0nC800000IotK0IAJ', 'a0nC800000J1iVYIAZ',
    'a0nC800000J24HXIAZ', 'a0nC800000MGuUbIAL', 'a0nC800000MGvorIAD',
    'a0nC800000MGwjJIAT', 'a0nC800000MTdEGIA1', 'a0nC800000MTm7pIAD',
    'a0nC800000JQsxuIAD', 'a0nC800000MTnS5IAL', 'a0nC800000MGIxGIAX',
    'a0nC800000Jwj3VIAR', 'a0nC800000Jn50SIAR', 'a0nC800000K3GuLIAV',
    'a0nC800000KDAywIAH', 'a0nC800000MGpQ5IAL',
]

LOGGER = logging.getLogger(__name__)


def fetch_claim_by_forceid(forceid: str) -> dict | None:
    """通过 forceid 精确查询单条案件，返回 claim 数据或 None"""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    auth_header = os.getenv("REST_AI_CLAIM_AUTH_HEADER", "").strip()
    if auth_header and ":" in auth_header:
        k, v = auth_header.split(":", 1)
        headers[k.strip()] = v.strip()

    try:
        resp = requests.post(API_URL, headers=headers, json={"forceid": forceid}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        LOGGER.error(f"  API请求失败 forceid={forceid}: {e}")
        return None

    # 解析返回：期望 { data: [...] } 或 { data: {...} } 或 [...]
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        for key in ("data", "items", "list", "records", "result"):
            val = data.get(key)
            if isinstance(val, list) and len(val) > 0:
                return val[0]
            if isinstance(val, dict) and (val.get("CaseNo") or val.get("forceid")):
                return val
    return None


def save_claim_info(claim: dict) -> tuple[bool, str]:
    """
    保存 claim_info.json 并写入 ai_claim_info_raw 表。
    返回 (成功标志, 消息)
    """
    forceid = str(claim.get("forceid") or "").strip()
    # CaseNo 通常为空，回退到 PolicyNo（与 download_claims.py 一致）
    case_no = str(
        claim.get("CaseNo") or claim.get("caseNo") or
        claim.get("PolicyNo") or claim.get("policyNo") or ""
    ).strip()
    benefit_name = str(claim.get("BenefitName") or claim.get("benefitName") or "").strip()
    applicant = str(claim.get("Applicant_Name") or claim.get("ApplicantName") or "").strip()

    if not case_no or not benefit_name:
        return False, f"缺少 CaseNo/PolicyNo 或 BenefitName (forceid={forceid})"

    # 确定目录路径
    case_dir = config.CLAIMS_DATA_DIR / benefit_name / f"{benefit_name}-案件号【{case_no}】"
    case_dir.mkdir(parents=True, exist_ok=True)

    # 处理 FileList 字段名统一
    claim_info = {k: v for k, v in claim.items()}
    for fkey in ("Files", "files", "Attachments", "attachments"):
        if fkey in claim_info and fkey != "FileList":
            claim_info["FileList"] = claim_info.pop(fkey)

    # 写入 claim_info.json
    claim_info_path = case_dir / "claim_info.json"
    with open(claim_info_path, "w", encoding="utf-8") as f:
        json.dump(claim_info, f, ensure_ascii=False, indent=4)

    # 写入 ai_claim_info_raw 表
    db_ok = True
    try:
        from scripts.download_claims import _save_claim_info_to_db
        _save_claim_info_to_db(claim_info)
    except Exception as db_err:
        db_ok = False
        LOGGER.warning(f"  写库失败 forceid={forceid}: {db_err}")

    return True, f"claim_info.json 已保存{' + 写库' if db_ok else ' (写库失败)'} " \
                 f"(CaseNo={case_no}, Benefit={benefit_name}, Applicant={applicant})"


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="按 forceid 补拉 claim_info.json")
    parser.add_argument("forceid", nargs="*", help="补拉指定 forceid（可多个）")
    parser.add_argument("--batch", type=str, help="从文件读取 forceid 列表（每行一个）")
    parser.add_argument("--duplicates", action="store_true", help="补拉29条已知重复案件")
    parser.add_argument("--dry-run", action="store_true", help="仅查询不保存")
    args = parser.parse_args()

    # 确定目标 forceid 列表
    target_forceids = []
    if args.forceid:
        target_forceids = args.forceid
    if args.batch:
        batch_file = Path(args.batch)
        if batch_file.exists():
            target_forceids.extend(
                line.strip() for line in batch_file.read_text().splitlines() if line.strip()
            )
        else:
            LOGGER.error(f"文件不存在: {batch_file}")
            sys.exit(1)
    if args.duplicates:
        target_forceids.extend(DUPLICATE_FORCEIDS)

    if not target_forceids:
        parser.print_help()
        sys.exit(1)

    # 去重
    target_forceids = list(dict.fromkeys(target_forceids))
    LOGGER.info(f"开始补拉 {len(target_forceids)} 个 forceid 的 claim_info...")

    success = 0
    fail = 0
    not_found = 0

    for i, forceid in enumerate(target_forceids, 1):
        LOGGER.info(f"[{i}/{len(target_forceids)}] 查询 forceid={forceid}...")
        claim = fetch_claim_by_forceid(forceid)

        if not claim:
            LOGGER.warning(f"  API 返回为空")
            not_found += 1
            continue

        actual_fid = str(claim.get("forceid") or "")
        if actual_fid != forceid:
            LOGGER.warning(f"  返回的 forceid 不匹配: 期望 {forceid}, 实际 {actual_fid}")

        if args.dry_run:
            cn = claim.get('CaseNo') or claim.get('PolicyNo')
            print(f"  [DRY RUN] CaseNo/PolicyNo={cn}, BenefitName={claim.get('BenefitName')}, Applicant={claim.get('Applicant_Name')}")
            success += 1
            continue

        ok, msg = save_claim_info(claim)
        if ok:
            LOGGER.info(f"  ✓ {msg}")
            success += 1
        else:
            LOGGER.error(f"  ✗ {msg}")
            fail += 1

    # 汇总
    print()
    LOGGER.info(f"补拉完成:")
    LOGGER.info(f"  成功: {success}")
    LOGGER.info(f"  失败: {fail}")
    LOGGER.info(f"  未找到: {not_found}")
    LOGGER.info(f"  合计: {len(target_forceids)}")


if __name__ == "__main__":
    main()
