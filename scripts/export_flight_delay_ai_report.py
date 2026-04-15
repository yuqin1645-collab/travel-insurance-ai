#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出航班延误 AI 审核结果到 Excel

输出列：
  ClaimId | PolicyNo | AI审核状态 | AI审核结论

用法：
  py scripts/export_flight_delay_ai_report.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REVIEW_DIR = ROOT / "review_results" / "flight_delay"
CLAIMS_DIR = ROOT / "claims_data"
OUTPUT_PATH = ROOT / "static" / "航班延误AI审核结果.xlsx"


def load_forceid_meta(claims_dir: Path) -> Dict[str, Dict[str, str]]:
    """遍历 claims_data，构建 forceid -> {ClaimId, PolicyNo} 映射"""
    meta: Dict[str, Dict[str, str]] = {}
    if not claims_dir.is_dir():
        return meta
    for info_file in claims_dir.rglob("claim_info.json"):
        if info_file.parent.name.startswith("."):
            continue
        try:
            data = json.loads(info_file.read_text(encoding="utf-8-sig"))
        except Exception:
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
            except Exception:
                continue
        fid = str(data.get("forceid") or "").strip()
        if not fid:
            continue
        meta[fid] = {
            "ClaimId": str(data.get("ClaimId") or "").strip(),
            "PolicyNo": str(data.get("PolicyNo") or data.get("Policy_No") or "").strip(),
        }
    return meta


def load_ai_results(review_dir: Path) -> Dict[str, Dict[str, Any]]:
    """加载所有 *_ai_review.json，键为 forceid"""
    results: Dict[str, Dict[str, Any]] = {}
    if not review_dir.is_dir():
        return results
    for f in review_dir.glob("*_ai_review.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except Exception:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
        fid = str(data.get("forceid") or "").strip()
        if fid:
            results[fid] = data
    return results


def get_audit_status(data: Dict[str, Any]) -> str:
    """AI审核状态：从 flight_delay_audit.audit_result 取"""
    audit = data.get("flight_delay_audit") or {}
    result = str(audit.get("audit_result") or "").strip()
    if result:
        return result
    # 兜底：从 IsAdditional 推断
    is_add = str(data.get("IsAdditional") or "").upper()
    if is_add == "Y":
        return "需补齐资料"
    if is_add == "N":
        remark = str(data.get("Remark") or "").lower()
        if "拒赔" in remark or "拒绝" in remark:
            return "拒绝"
        if "通过" in remark or "赔付" in remark:
            return "通过"
    return "未知"


def get_audit_explanation(data: Dict[str, Any]) -> str:
    """AI审核结论：从 flight_delay_audit.explanation 取，兜底用 Remark"""
    audit = data.get("flight_delay_audit") or {}
    exp = str(audit.get("explanation") or "").strip()
    if exp:
        return exp
    return str(data.get("Remark") or "").strip()


def main() -> int:
    print("=" * 60)
    print("导出航班延误 AI 审核结果")
    print("=" * 60)

    meta = load_forceid_meta(CLAIMS_DIR)
    ai_results = load_ai_results(REVIEW_DIR)

    print(f"claims_data 映射: {len(meta)} 条")
    print(f"AI 审核结果: {len(ai_results)} 条\n")

    rows = []
    for fid, data in ai_results.items():
        m = meta.get(fid, {})
        rows.append({
            "forceid": fid,
            "ClaimId": m.get("ClaimId", ""),
            "PolicyNo": m.get("PolicyNo", ""),
            "AI审核状态": get_audit_status(data),
            "AI审核结论": get_audit_explanation(data),
        })

    df = pd.DataFrame(rows, columns=["forceid", "ClaimId", "PolicyNo", "AI审核状态", "AI审核结论"])
    df.sort_values("ClaimId", inplace=True, ignore_index=True)

    print(f"共 {len(df)} 条记录")
    print("审核状态分布：")
    print(df["AI审核状态"].value_counts().to_string())
    print()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OUTPUT_PATH.with_suffix(".tmp.xlsx")
    try:
        df.to_excel(tmp_path, index=False)
        os.replace(tmp_path, OUTPUT_PATH)
        print(f"已生成: {OUTPUT_PATH}")
    except PermissionError:
        ts = time.strftime("%Y%m%d_%H%M%S")
        fallback = OUTPUT_PATH.with_name(f"{OUTPUT_PATH.stem}_{ts}{OUTPUT_PATH.suffix}")
        if tmp_path.exists():
            os.replace(tmp_path, fallback)
        else:
            df.to_excel(fallback, index=False)
        print(f"目标文件被占用，已输出到: {fallback}")
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
