#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 forceid 或 ClaimId 查找案件所在路径（交互式）

用法:
  py scripts/find_claim_by_forceid.py
  运行后输入 forceid 或 ClaimId，回车即显示地址；输入 q 或直接回车退出
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DIR = ROOT / "claims_data"
REVIEW_DIR = ROOT / "review_results"
CACHE_DIR = ROOT / ".cache" / "ocr"


def find_claim_path(query, absolute=False):
    """
    根据 forceid 或 ClaimId 查找案件路径。
    返回: {"claims_dir": Path, "review_file": Path, "matched_by": str} 或 None
    策略：
      1. 优先按文件名（forceid）查找
      2. 如果找不到，扫描所有 review_json，通过 ClaimId 字段查找
      3. 只要找到审核结果就返回（不要求必须找到案件材料）
    """
    query = query.strip()
    if not query:
        return None

    claims_path = None
    review_file = None
    matched_forceid = None
    matched_by = None

    # === 第一步：按 forceid（文件名）查找 ===
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

    # === 第二步：如果还没找到，扫描所有审核结果JSON，通过 ClaimId 字段匹配 ===
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

    # === 第三步：如果还没找到，尝试在 claims_data 中通过 ClaimId 找 forceid（兜底）===
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

    # OCR cache：只做"是否存在缓存记录"的辅助定位（缓存按 hash 命名，forceid 不直接关联）
    cache_dir = str(CACHE_DIR.resolve()) if absolute else str(CACHE_DIR)

    # 只要找到审核结果就返回（不要求必须找到案件材料）
    if review_file is None:
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
        "ocr_cache_dir": cache_dir,
    }


def main():
    parser = argparse.ArgumentParser(description="根据 forceid 或 ClaimId 查找案件路径（交互式）")
    parser.add_argument("--absolute", "-a", action="store_true", help="输出绝对路径")
    args = parser.parse_args()

    print("=" * 50)
    print("根据 forceid 或 ClaimId 查找案件路径")
    print("输入 forceid 或 ClaimId 回车查询，输入 q 或直接回车退出")
    print("=" * 50)

    while True:
        try:
            query = input("\n请输入 forceid 或 ClaimId: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if not query or query.lower() == "q":
            print("再见")
            break

        result = find_claim_path(query, absolute=args.absolute)
        if result is None:
            print("未找到 forceid/ClaimId={} 对应的案件".format(query))
            continue

        print("\n匹配方式: {}".format(result['matched_by']))
        print("forceid: {}".format(result['forceid']))
        if result["claims_dir"]:
            print("案件数据目录: {}".format(result["claims_dir"]))
        else:
            print("案件数据目录: (未找到)")
        if result["review_file"]:
            print("审核结果文件: {}".format(result["review_file"]))
        else:
            print("审核结果文件: (未找到)")
        print("OCR缓存目录: {}".format(result["ocr_cache_dir"]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
