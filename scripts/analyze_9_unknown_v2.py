#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析9件 delay_hours=unknown 的案件（输出到文件避免编码问题）
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

CLAIMS_DIR = ROOT / "claims_data"
REVIEW_DIR = ROOT / "review_results" / "baggage_delay"

UNKNOWN_CASES = [
    "a0nC800000IdwOPIAZ", "a0nC800000ICHXNIA5", "a0nC800000IBWjlIAH",
    "a0nC800000Ib5ZTIAZ", "a0nC800000I9eOxIAJ", "a0nC800000I9eOuIAJ",
    "a0nC800000I7iQHIAZ", "a0nC800000HqpJBIAZ", "a0nC800000JZU0NIAX",
]

def find_claim_folder(forceid):
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                return info_file.parent
        except Exception:
            continue
    return None

output_lines = []

def p(text=""):
    output_lines.append(str(text))

for fid in UNKNOWN_CASES:
    folder = find_claim_folder(fid)
    p(f"\n{'='*60}")
    p(f"案件: {fid}")
    p(f"{'='*60}")

    if not folder:
        p("  [未找到目录]")
        continue

    info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
    p(f"  BenefitName: {info.get('BenefitName', '')}")
    desc = str(info.get('Description', ''))[:300]
    p(f"  Description: {desc}")
    p(f"  Final_Status: {info.get('Final_Status', '')}")

    review_file = REVIEW_DIR / f"{fid}_ai_review.json"
    if review_file.exists():
        review = json.loads(review_file.read_text(encoding="utf-8"))
        p(f"\n  审核结论: {review.get('audit_result', '')}")
        p(f"  Remark: {review.get('Remark', '')[:200]}")
        p(f"  Payout: {review.get('payout_amount', '')}")

        kc = review.get('KeyConclusions', {})
        if isinstance(kc, dict):
            for k, v in kc.items():
                p(f"  KC[{k}]: {v}")

        debug = review.get('DebugInfo', {})
        ai_parsed = debug.get('ai_parsed', {})
        p(f"\n  ai_parsed:")
        p(f"    has_baggage_tag_proof: {ai_parsed.get('has_baggage_tag_proof')}")
        p(f"    has_baggage_delay_proof: {ai_parsed.get('has_baggage_delay_proof')}")
        p(f"    has_baggage_receipt_time_proof: {ai_parsed.get('has_baggage_receipt_time_proof')}")
        p(f"    delay_hours: {ai_parsed.get('delay_hours')}")
        p(f"    baggage_delay_hours: {ai_parsed.get('baggage_delay_hours')}")

        # 查看 vision_extract
        vision_ext = debug.get('vision_extract', {})
        if isinstance(vision_ext, dict):
            p(f"\n  vision_extract 字段:")
            for k, v in vision_ext.items():
                p(f"    {k}: {v}")

    # 列出材料文件
    files = []
    for f in folder.iterdir():
        if f.is_file() and f.name != "claim_info.json":
            ext = f.suffix.lower()
            size = f.stat().st_size
            files.append((f.name, ext, size))
    p(f"\n  材料文件 ({len(files)}个):")
    for name, ext, size in sorted(files):
        size_kb = size / 1024
        p(f"    {name}  ({ext}, {size_kb:.0f}KB)")

    # 读取vision结果JSON - 尝试多种模式
    for vf in folder.iterdir():
        if vf.is_file() and vf.suffix.lower() == '.json' and vf.name != "claim_info.json":
            try:
                vdata = json.loads(vf.read_text(encoding="utf-8"))
                if isinstance(vdata, dict):
                    # 搜索签收时间相关字段
                    for k in vdata:
                        kl = k.lower()
                        if any(kw in kl for kw in ["receipt", "签收", "领取", "baggage_claim", "arrive"]):
                            p(f"\n  [{vf.name}] 字段 {k} = {vdata[k]}")
            except Exception:
                pass

p("\n\n=== 完成 ===")

out_path = ROOT / "docs" / "9_unknown_analysis.txt"
out_path.write_text("\n".join(output_lines), encoding="utf-8")
print(f"已写入 {out_path}")
