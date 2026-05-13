#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析9件 delay_hours=unknown 的案件：
- 读取每个案件的 claim_info.json 获取材料列表
- 读取审核结果 JSON 获取 KeyConclusions
- 列出这些案件有哪些材料文件
- 尝试从材料中提取与签收时间相关的信息
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

for fid in UNKNOWN_CASES:
    folder = find_claim_folder(fid)
    print(f"\n{'='*60}")
    print(f"案件: {fid}")
    print(f"{'='*60}")

    if not folder:
        print("  [未找到目录]")
        continue

    # 1. 读取 claim_info
    info = json.loads((folder / "claim_info.json").read_text(encoding="utf-8"))
    print(f"  BenefitName: {info.get('BenefitName', '')}")
    print(f"  Description: {str(info.get('Description', ''))[:200]}")
    print(f"  Final_Status: {info.get('Final_Status', '')}")

    # 2. 读取审核结果
    review_file = REVIEW_DIR / f"{fid}_ai_review.json"
    if review_file.exists():
        review = json.loads(review_file.read_text(encoding="utf-8"))
        print(f"\n  审核结论: {review.get('audit_result', '')}")
        print(f"  Remark: {review.get('Remark', '')[:200]}")
        payout = review.get('payout_amount', '')
        print(f"  Payout: {payout}")

        # KeyConclusions
        kc = review.get('KeyConclusions', {})
        if isinstance(kc, dict):
            for k, v in kc.items():
                print(f"  KeyConclusions[{k}]: {v}")

        # DebugInfo
        debug = review.get('DebugInfo', {})
        ai_parsed = debug.get('ai_parsed', {})
        print(f"\n  ai_parsed 关键字段:")
        print(f"    has_baggage_tag_proof: {ai_parsed.get('has_baggage_tag_proof')}")
        print(f"    has_baggage_delay_proof: {ai_parsed.get('has_baggage_delay_proof')}")
        print(f"    has_baggage_receipt_time_proof: {ai_parsed.get('has_baggage_receipt_time_proof')}")
        print(f"    delay_hours: {ai_parsed.get('delay_hours')}")
        print(f"    baggage_delay_hours: {ai_parsed.get('baggage_delay_hours')}")

    # 3. 列出材料文件
    files = [f.name for f in folder.iterdir() if f.is_file() and f.name != "claim_info.json"]
    print(f"\n  材料文件 ({len(files)}个):")
    for f in sorted(files):
        print(f"    - {f}")

    # 4. 尝试读取 vision 结果（如果有）
    vision_files = list(folder.glob("*vision*"))
    for vf in vision_files:
        try:
            vdata = json.loads(vf.read_text(encoding="utf-8"))
            # 查找与签收时间相关的字段
            text = json.dumps(vdata, ensure_ascii=False)
            for keyword in ["签收", "领取", "到达", "arrival", "receive", "deliver", "baggage_claim", "time"]:
                if keyword in text.lower():
                    print(f"\n  [{vf.name}] 含 '{keyword}' 相关内容（前300字符）:")
                    # 简单搜索
                    idx = text.lower().index(keyword.lower())
                    snippet = text[max(0,idx-100):idx+200]
                    print(f"    ...{snippet}...")
                    break
        except Exception:
            pass

print("\n\n完成")
