#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 审核结论回传工具
读取本地 claim_info.json，将 AI 结论 POST 到 Rest_AI_CLaim_Conclusion 接口
支持 --dry-run 模式只打印不发送
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import requests
except ImportError:
    print("请先安装: pip install requests")
    sys.exit(1)

CONCLUSION_API_URL = os.getenv("CONCLUSION_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim_Conclusion")
CLAIMS_DATA_DIR = ROOT / "claims_data"


def load_dotenv_if_exists() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            pass


def iter_claim_infos(claims_dir: Path):
    """遍历所有 claim_info.json"""
    for f in claims_dir.rglob("claim_info.json"):
        if f.parent.name.startswith("."):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            yield f, data
        except Exception as e:
            print(f"[跳过] 读取失败 {f}: {e}")


def build_payload(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 claim_info 构建回传 payload，缺少 forceid 则返回 None"""
    forceid = (info.get("forceid") or info.get("ForceId") or "").strip()
    if not forceid:
        return None

    remark = (info.get("Assessment_Remark") or info.get("Remark") or "").strip()
    is_additional = (info.get("IsAdditional") or "N").strip()
    final_status = str(info.get("Final_Status") or "")

    # 构建 KeyConclusions
    key_conclusions: List[Dict[str, str]] = []

    # 如果有拒赔金额，作为一个核对点写入
    rejected_amount = info.get("Rejected_Amount")
    if rejected_amount:
        if info.get("Reserved_Amount") and float(info.get("Reserved_Amount", 0)) > 0:
            eligible = "N"
            checkpoint = f"部分赔付，拒赔金额：{rejected_amount} 元"
        else:
            eligible = "N"
            checkpoint = f"全额拒赔，拒赔金额：{rejected_amount} 元"
        key_conclusions.append({
            "checkpoint": checkpoint,
            "Eligible": eligible,
            "Remark": remark,
        })
    else:
        key_conclusions.append({
            "checkpoint": "",
            "Eligible": "Y" if "支付" in final_status else "",
            "Remark": remark,
        })

    return {
        "forceid": forceid,
        "Remark": remark,
        "IsAdditional": is_additional,
        "KeyConclusions": key_conclusions,
    }


def post_conclusions(payloads: List[Dict], api_url: str, timeout: int = 30) -> bool:
    """批量 POST 回传，返回是否成功"""
    try:
        resp = requests.post(api_url, json=payloads, timeout=timeout)
        resp.raise_for_status()
        result = resp.json()
        code = result.get("code") if isinstance(result, dict) else None
        if code == 200:
            return True
        print(f"[警告] 接口返回非 200: {result}")
        return False
    except requests.RequestException as e:
        print(f"[错误] 回传请求失败: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="将 AI 审核结论回传到 Rest_AI_CLaim_Conclusion 接口")
    parser.add_argument("--dry-run", action="store_true", help="只打印 payload，不发送请求")
    parser.add_argument("--api-url", default=CONCLUSION_API_URL, help="回传接口地址")
    parser.add_argument("--claims-dir", default=str(CLAIMS_DATA_DIR), help="本地案件数据目录")
    parser.add_argument("--batch-size", type=int, default=20, help="每批发送条数，默认 20")
    args = parser.parse_args()

    load_dotenv_if_exists()
    claims_dir = Path(args.claims_dir)

    payloads: List[Dict] = []
    skipped = 0

    for f, info in iter_claim_infos(claims_dir):
        payload = build_payload(info)
        if not payload:
            skipped += 1
            continue
        payloads.append(payload)

    print(f"共找到 {len(payloads)} 条可回传记录，跳过 {skipped} 条（缺少 forceid）")

    if not payloads:
        print("无可回传数据，退出。")
        return 0

    if args.dry_run:
        print("\n[dry-run] 将发送以下 payload：")
        print(json.dumps(payloads[:3], ensure_ascii=False, indent=2))
        if len(payloads) > 3:
            print(f"  ... 共 {len(payloads)} 条")
        return 0

    # 分批发送
    success = 0
    for i in range(0, len(payloads), args.batch_size):
        batch = payloads[i:i + args.batch_size]
        print(f"发送第 {i + 1}~{i + len(batch)} 条...")
        if post_conclusions(batch, args.api_url):
            success += len(batch)
            print(f"  ✓ 成功")
        else:
            print(f"  ✗ 失败")

    print(f"\n回传完成：成功 {success}/{len(payloads)} 条")
    return 0 if success == len(payloads) else 1


if __name__ == "__main__":
    sys.exit(main())
