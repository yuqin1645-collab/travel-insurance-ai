#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计 review_results 审核结果分布与主要原因

用法:
  py scripts/analyze_review_results.py
  py scripts/analyze_review_results.py --source api_response.json
  py scripts/analyze_review_results.py --source review_results
  py scripts/analyze_review_results.py --top 20
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]


def _load_from_api_response(path: Path) -> List[Dict[str, Any]]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    data = obj.get("data")
    if not isinstance(data, list):
        raise ValueError(f"{path} 不是标准 api_response 格式: 缺少 data[]")
    out: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and item.get("forceid"):
            out.append(item)
    return out


def _load_from_review_dir(dir_path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for f in sorted(dir_path.rglob("*_ai_review.json")):
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(item, dict) and item.get("forceid"):
            out.append(item)
    return out


def _category(item: Dict[str, Any]) -> str:
    remark = str(item.get("Remark") or "")
    is_add = str(item.get("IsAdditional") or "")

    if is_add.upper() == "Y":
        if "Vision模式失败" in remark or "材料审核系统异常" in remark:
            return "补件/人工:Vision或系统异常"
        if "需要人工审核" in remark:
            return "补件/人工:需要人工审核"
        if "需要补充材料" in remark:
            return "补件/人工:缺件"
        return "补件/人工:其他"

    # IsAdditional == N
    if "拒赔" in remark:
        return "拒赔"
    if "赔付" in remark or "通过" in remark:
        return "通过/赔付(文本命中)"
    return "终结:N但非拒赔"


def _reject_reason_bucket(remark: str) -> str:
    """
    对拒赔原因做粗分桶（便于看主因）
    """
    r = remark
    buckets: List[Tuple[str, List[str]]] = [
        ("保额/限额", ["超过剩余保额", "超过保额", "限额"]),
        ("保单有效期", ["不在保单有效期内", "有效期"]),
        ("出行时间早于生效(事后投保)", ["最早出行日期", "保单自始无效", "出境后才投保", "出行时间早于投保"]),
        ("除外责任", ["除外", "免责", "触发除外责任"]),
        ("不属保障范围/责任", ["不属于保障范围", "不符合保障责任"]),
        ("赔付金额为0", ["赔偿金额为0", "无可赔付金额", "金额为0"]),
    ]
    for name, keys in buckets:
        if any(k in r for k in keys):
            return name
    return "其他拒赔原因"


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 review_results 审核结果分布与原因")
    parser.add_argument(
        "--source",
        default="review_results",
        help="数据源: review_results(目录) 或 api_response.json(文件，默认在 review_results 下)",
    )
    parser.add_argument("--top", type=int, default=15, help="展示 TopN 典型 forceid")
    args = parser.parse_args()

    src = args.source.strip()
    if src.endswith(".json"):
        path = (ROOT / "review_results" / src) if not Path(src).is_absolute() else Path(src)
        items = _load_from_api_response(path)
        source_name = str(path)
    else:
        dir_path = (ROOT / src) if not Path(src).is_absolute() else Path(src)
        items = _load_from_review_dir(dir_path)
        source_name = str(dir_path)

    if not items:
        print("未找到任何审核结果。")
        return 1

    total = len(items)
    cat_counter = Counter()
    reject_bucket_counter = Counter()
    examples: Dict[str, List[str]] = defaultdict(list)

    for it in items:
        fid = str(it.get("forceid"))
        remark = str(it.get("Remark") or "")
        cat = _category(it)
        cat_counter[cat] += 1
        if cat == "拒赔":
            rb = _reject_reason_bucket(remark)
            reject_bucket_counter[rb] += 1
            if len(examples[f"拒赔:{rb}"]) < args.top:
                examples[f"拒赔:{rb}"].append(fid)
        else:
            if len(examples[cat]) < args.top:
                examples[cat].append(fid)

    print("=" * 60)
    print("审核结果统计")
    print("=" * 60)
    print(f"数据源: {source_name}")
    print(f"总数: {total}")
    print()

    print("分布:")
    for k, v in cat_counter.most_common():
        print(f"  - {k}: {v} ({v/total:.1%})")
    print()

    if cat_counter.get("拒赔"):
        print("拒赔原因分桶:")
        for k, v in reject_bucket_counter.most_common():
            print(f"  - {k}: {v} ({v/cat_counter['拒赔']:.1%})")
        print()

    print(f"典型 forceid（每类最多 {args.top} 个）:")
    for k in sorted(examples.keys()):
        print(f"  - {k}: {', '.join(examples[k])}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

