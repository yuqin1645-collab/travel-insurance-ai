#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按 forceid 查询案件并下载附件到 claims_data 目录。

交互式用法（推荐）：
  python scripts/fetch_claim_by_forceid.py
  → 提示输入 forceid，支持空格/逗号分隔多个

命令行用法：
  python scripts/fetch_claim_by_forceid.py a0nC800000MOLuhIAH
  python scripts/fetch_claim_by_forceid.py id1 id2 id3
"""

import sys
import json
import logging
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_URL = "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim"
OUTPUT_DIR = "claims_data"

logging.basicConfig(level=logging.INFO, format="%(message)s")


def fetch_by_forceid(forceid: str) -> dict:
    resp = requests.post(API_URL, json={"forceid": forceid}, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    # 形如 {"code": 200, "data": [...]} 或 {"code": 200, "data": {...}}
    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        if isinstance(inner, list):
            if not inner:
                raise ValueError(f"未找到案件: forceid={forceid}")
            return inner[0]
        elif isinstance(inner, dict):
            return inner

    if isinstance(data, list):
        if not data:
            raise ValueError(f"未找到案件: forceid={forceid}")
        return data[0]

    # API 直接返回单条案件 dict（有 CaseNo / forceid 字段）
    if isinstance(data, dict) and (data.get("CaseNo") or data.get("forceid") or data.get("BenefitName")):
        return data

    raise ValueError(f"未识别的 API 返回格式: keys={list(data.keys()) if isinstance(data, dict) else type(data)}")


def process_one(forceid: str) -> None:
    from scripts.download_claims import ClaimDownloader

    forceid = forceid.strip()
    if not forceid:
        return

    print(f"\n{'='*50}")
    print(f"查询 forceid={forceid} ...")
    try:
        claim = fetch_by_forceid(forceid)
    except Exception as e:
        print(f"[错误] 查询失败: {e}")
        return

    benefit = claim.get("BenefitName") or claim.get("benefitName") or "未知险种"
    case_no = claim.get("CaseNo") or claim.get("caseNo") or claim.get("ClaimId") or forceid
    print(f"险种: {benefit}  案件号: {case_no}  字段keys: {list(claim.keys())[:10]}")

    downloader = ClaimDownloader(api_url=API_URL, output_dir=OUTPUT_DIR, force_refresh=False)
    downloader.process_claim(claim)


def parse_forceids(raw: str) -> list[str]:
    """支持空格、逗号、换行混合分隔"""
    import re
    return [x for x in re.split(r"[\s,，]+", raw) if x]


def main():
    if len(sys.argv) > 1:
        forceids = parse_forceids(" ".join(sys.argv[1:]))
    else:
        print("请输入 forceid（多个用空格或逗号分隔，输入 q 退出）：")
        forceids = []
        while True:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if raw.lower() in ("q", "quit", "exit"):
                break
            ids = parse_forceids(raw)
            if ids:
                forceids.extend(ids)
                break

    if not forceids:
        print("未输入任何 forceid，退出。")
        return

    print(f"\n共 {len(forceids)} 个案件待处理: {forceids}")
    for fid in forceids:
        process_one(fid)

    print(f"\n全部处理完成。文件已保存到 {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
