#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 forceid 或 ClaimId 查找案件路径 / 从API拉取单个案件

用法:
  py scripts/find_claim_by_forceid.py           # 交互式查询
  py scripts/find_claim_by_forceid.py xxx       # 直接查询指定 forceid

作为模块导入:
  from scripts.find_claim_by_forceid import fetch_by_forceid
  claim_data = fetch_by_forceid("a0nC800000HcbG5IAJ")
"""

import argparse
import json
import os
import sys
import requests
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DIR = ROOT / "claims_data"
REVIEW_DIR = ROOT / "review_results"

# ─────────────────────────────────────────────
# API 拉取单个案件
# ─────────────────────────────────────────────

API_URL = os.getenv("CLAIMS_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim")


def fetch_by_forceid(forceid: str, api_url: str = API_URL) -> Dict:
    """从 API 按 forceid 拉取单个案件数据，返回 claim dict"""
    # 先尝试只传 forceid 精确查询
    resp = requests.post(api_url, json={"forceid": forceid}, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    claims = []
    if isinstance(raw, list):
        claims = raw
    elif isinstance(raw, dict):
        claims = raw.get("records") or raw.get("data") or raw.get("claims") or []
        # 单条返回
        if not claims and (raw.get("forceid") or raw.get("Id")):
            claims = [raw]

    for c in claims:
        if str(c.get("forceid") or c.get("Id") or "") == forceid:
            return c

    # 精确查询未命中，降级为全量拉取再匹配
    resp = requests.post(api_url, json={}, timeout=30)
    resp.raise_for_status()
    raw = resp.json()
    if isinstance(raw, list):
        claims = raw
    elif isinstance(raw, dict):
        claims = raw.get("records") or raw.get("data") or raw.get("claims") or []
    else:
        claims = []
    for c in claims:
        if str(c.get("forceid") or c.get("Id") or "") == forceid:
            return c
    raise ValueError(f"API 未返回 forceid={forceid} 的案件数据")


# ─────────────────────────────────────────────
# 交互式查询
# ─────────────────────────────────────────────

CACHE_DIR = ROOT / ".cache" / "ocr"


def find_claim_path(query, absolute=False):
    """根据 forceid 或 ClaimId 查找案件路径"""
    query = query.strip()
    if not query:
        return None

    claims_path = None
    review_file = None
    matched_forceid = None
    matched_by = None

    # 按 forceid（文件名）查找
    ns_candidates = list(REVIEW_DIR.glob("**/{}_ai_review.json".format(query)))
    if ns_candidates:
        review_file = sorted(ns_candidates, key=lambda p: len(str(p)))[0]
        matched_forceid = query
        matched_by = "forceid"
    else:
        flat = REVIEW_DIR / "{}_ai_review.json".format(query)
        if flat.exists():
            review_file = flat
            matched_forceid = query
            matched_by = "forceid"

    # 扫描审核结果JSON，通过 ClaimId 匹配
    if review_file is None:
        for json_file in REVIEW_DIR.rglob("*_ai_review.json"):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            file_claimid = str(data.get("ClaimId") or "").strip()
            if file_claimid == query:
                review_file = json_file
                matched_forceid = data.get("forceid")
                matched_by = "ClaimId"
                break

    # 在 claims_data 中通过 ClaimId 找 forceid
    if review_file is None:
        for info_file in CLAIMS_DIR.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(data.get("ClaimId") or "").strip() == query:
                claims_path = info_file.parent
                candidate_forceid = data.get("forceid")
                if candidate_forceid:
                    ns_candidates = list(REVIEW_DIR.glob("**/{}_ai_review.json".format(candidate_forceid)))
                    if ns_candidates:
                        review_file = sorted(ns_candidates, key=lambda p: len(str(p)))[0]
                    else:
                        flat = REVIEW_DIR / "{}_ai_review.json".format(candidate_forceid)
                        review_file = flat if flat.exists() else None
                    if review_file:
                        matched_forceid = candidate_forceid
                        matched_by = "ClaimId"
                break

    # 根据 matched_forceid 找案件目录
    if claims_path is None and matched_forceid:
        for info_file in CLAIMS_DIR.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(data.get("forceid") or "").strip() == matched_forceid:
                claims_path = info_file.parent
                break

    # 兜底：直接在 claims_data 里扫描
    if review_file is None and claims_path is None:
        for info_file in CLAIMS_DIR.rglob("claim_info.json"):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            fid = str(data.get("forceid") or "").strip()
            cid = str(data.get("ClaimId") or "").strip()
            if fid == query or cid == query:
                claims_path = info_file.parent
                matched_forceid = fid or query
                matched_by = "forceid" if fid == query else "ClaimId"
                break

    if review_file is None and claims_path is None:
        return None

    def to_str(p):
        if p is None:
            return None
        return str(p.resolve()) if absolute else str(p.relative_to(ROOT))

    return {
        "forceid": matched_forceid or query,
        "matched_by": matched_by or "forceid",
        "claims_dir": to_str(claims_path),
        "review_file": to_str(review_file),
        "ocr_cache_dir": str(CACHE_DIR.resolve()) if absolute else str(CACHE_DIR),
    }


def main():
    parser = argparse.ArgumentParser(description="根据 forceid 或 ClaimId 查找案件路径")
    parser.add_argument("query", nargs="?", help="forceid 或 ClaimId")
    parser.add_argument("--absolute", "-a", action="store_true", help="输出绝对路径")
    args = parser.parse_args()

    if args.query:
        result = find_claim_path(args.query, absolute=args.absolute)
        if result is None:
            print(f"未找到: {args.query}")
            return 1
        for k, v in result.items():
            print(f"  {k}: {v}")
        return 0

    # 交互式
    print("输入 forceid 或 ClaimId 回车查询，输入 q 退出")
    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query or query.lower() == "q":
            break
        result = find_claim_path(query, absolute=args.absolute)
        if result is None:
            print("未找到")
            continue
        for k, v in result.items():
            print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
