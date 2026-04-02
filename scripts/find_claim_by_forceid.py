#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根据 forceid 查找案件所在路径（交互式）

用法:
  py scripts/find_claim_by_forceid.py
  运行后输入 forceid，回车即显示地址；输入 q 或直接回车退出
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DIR = ROOT / "claims_data"
REVIEW_DIR = ROOT / "review_results"
CACHE_DIR = ROOT / ".cache" / "ocr"


def find_claim_path(query: str, absolute: bool = False) -> dict | None:
    """
    根据 forceid 或 ClaimId 查找案件路径。
    返回: {"claims_dir": Path, "review_file": Path, "matched_by": str} 或 None
    """
    query = query.strip()
    if not query:
        return None

    claims_path = None
    matched_forceid = None
    matched_by = None

    # claims_data 兼容两种结构：
    # 1) 平铺：claims_data/<case_folder>/claim_info.json
    # 2) 命名空间：claims_data/<claim_type>/<case_folder>/claim_info.json
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("forceid") == query:
            claims_path = info_file.parent
            matched_forceid = query
            matched_by = "forceid"
            break
        if str(data.get("ClaimId") or "").strip() == query:
            claims_path = info_file.parent
            matched_forceid = data.get("forceid")
            matched_by = "ClaimId"
            break

    # 若通过 ClaimId 找到了 forceid，用真实 forceid 查审核结果
    forceid = matched_forceid or query

    # review_results 支持命名空间目录：review_results/<claim_type>/*_ai_review.json
    review_file = None
    ns_candidates = list(REVIEW_DIR.glob(f"**/{forceid}_ai_review.json"))
    if ns_candidates:
        # 优先选择最短路径（通常是 review_results/<claim_type>/...）
        review_file = sorted(ns_candidates, key=lambda p: len(str(p)))[0]
    else:
        flat = REVIEW_DIR / f"{forceid}_ai_review.json"
        review_file = flat if flat.exists() else None

    # OCR cache：只做“是否存在缓存记录”的辅助定位（缓存按 hash 命名，forceid 不直接关联）
    cache_dir = str(CACHE_DIR.resolve()) if absolute else str(CACHE_DIR)

    if claims_path is None and review_file is None:
        return None

    def to_str(p: Path | None) -> str | None:
        if p is None:
            return None
        return str(p.resolve()) if absolute else str(p.relative_to(ROOT))

    return {
        "forceid": forceid,
        "matched_by": matched_by or "forceid",
        "claims_dir": to_str(claims_path),
        "review_file": to_str(review_file),
        "ocr_cache_dir": cache_dir,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="根据 forceid 查找案件路径（交互式）")
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
            print(f"未找到 forceid/ClaimId={query} 对应的案件")
            continue

        print(f"\n匹配方式: {result['matched_by']}")
        print(f"forceid: {result['forceid']}")
        if result["claims_dir"]:
            print(f"案件数据目录: {result['claims_dir']}")
        else:
            print("案件数据目录: (未找到)")
        if result["review_file"]:
            print(f"审核结果文件: {result['review_file']}")
        else:
            print("审核结果文件: (未找到)")
        print(f"OCR缓存目录: {result['ocr_cache_dir']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
