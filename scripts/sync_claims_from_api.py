#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 Rest_AI_CLaim 接口获取「有材料」案件列表，与本地 claims_data 对齐：
- 仅保留接口返回的案件，其余案件目录删除
- 将接口返回的 ClaimId 写入每个案件目录下的 claim_info.json

用法:
  py scripts/sync_claims_from_api.py
  py scripts/sync_claims_from_api.py --dry-run   # 只打印将要做的操作，不删不改
  py scripts/sync_claims_from_api.py --api-url "https://custom-url/..."
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

# 默认接口地址（可被环境变量或 --api-url 覆盖）
DEFAULT_API_URL = "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim"


def load_dotenv_if_exists() -> None:
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            pass


def fetch_claim_list(api_url: str, timeout: int = 30) -> List[Dict[str, Any]]:
    """请求接口，返回案件列表。期望为 JSON 数组或 { data: [] } 等形式。"""
    load_dotenv_if_exists()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    # 若接口需要鉴权，可从环境变量读，例如: REST_AI_CLAIM_AUTH_HEADER="Authorization: Bearer xxx"
    auth_header = os.getenv("REST_AI_CLAIM_AUTH_HEADER", "").strip()
    if auth_header and ":" in auth_header:
        k, v = auth_header.split(":", 1)
        headers[k.strip()] = v.strip()

    resp = requests.post(api_url, headers=headers, json={}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "list", "claims", "result"):
            if key in data and isinstance(data[key], list):
                return data[key]
        # 若整包是单条
        if "ClaimId" in data or "claimId" in data:
            return [data]
    return []


def normalize_claim_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    从一条 API 记录中提取 ClaimId 与用于匹配本地的 key（forceid 或 PolicyNo）。
    返回 {"claim_id": str, "forceid": str|None, "policy_no": str|None} 或 None。
    """
    claim_id = record.get("ClaimId") or record.get("claimId")
    if claim_id is None:
        return None
    claim_id = str(claim_id).strip()
    if not claim_id:
        return None

    forceid = record.get("forceid") or record.get("ForceId") or record.get("forceId")
    if forceid is not None:
        forceid = str(forceid).strip()
    else:
        forceid = None

    policy_no = record.get("PolicyNo") or record.get("policyNo") or record.get("policy_no")
    if policy_no is not None:
        policy_no = str(policy_no).strip()
    else:
        policy_no = None

    return {"claim_id": claim_id, "forceid": forceid, "policy_no": policy_no}


def build_api_lookup(api_list: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    建立 本地匹配键 -> ClaimId 的映射。
    优先用 forceid 匹配，其次用 PolicyNo（一个 PolicyNo 可能对应多条，取第一条的 ClaimId）。
    """
    lookup: Dict[str, str] = {}
    by_policy: Dict[str, str] = {}

    for record in api_list:
        norm = normalize_claim_record(record)
        if not norm:
            continue
        cid = norm["claim_id"]
        if norm["forceid"]:
            lookup[norm["forceid"]] = cid
        if norm["policy_no"] and norm["policy_no"] not in by_policy:
            by_policy[norm["policy_no"]] = cid

    # PolicyNo 仅在没有 forceid 匹配时用
    for k, v in by_policy.items():
        if k not in lookup:
            lookup[k] = v
    return lookup


def get_claims_data_dir() -> Path:
    from app.config import config
    p = Path(config.CLAIMS_DATA_DIR)
    if not p.is_absolute():
        p = ROOT / p
    return p


def resolve_claims_scope_dir(claims_dir: Path, claim_type: str) -> Path:
    """
    为了避免未来多险种/多模块共用 claims_data 时误删：
    - 若 claims_data/<claim_type>/ 存在，则只在该子目录内同步/删除
    - 否则回退到 claims_data/（兼容当前平铺结构）
    """
    ct = (claim_type or "").strip()
    if not ct:
        return claims_dir
    scoped = claims_dir / ct
    return scoped if scoped.is_dir() else claims_dir


def iter_claim_folders(claims_dir: Path):
    """遍历所有案件目录（每个目录需包含 claim_info.json）。"""
    if not claims_dir.is_dir():
        return
    for info_file in claims_dir.rglob("claim_info.json"):
        folder = info_file.parent
        if folder.name.startswith("."):
            continue
        yield folder


def read_claim_info(folder: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(folder / "claim_info.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="按 Rest_AI_CLaim 接口结果同步本地案件并写入 ClaimId")
    parser.add_argument("--dry-run", action="store_true", help="仅打印将要执行的操作，不删除、不写入")
    parser.add_argument("--api-url", default="", help="接口地址，默认从环境变量 REST_AI_CLAIM_URL 或内置默认值")
    parser.add_argument(
        "--claim-type",
        default=os.getenv("CLAIM_TYPE", "baggage_damage"),
        help="案件类型/模块命名空间（用于安全限定删除范围），默认 baggage_damage",
    )
    args = parser.parse_args()

    load_dotenv_if_exists()
    api_url = args.api_url or os.getenv("REST_AI_CLAIM_URL", DEFAULT_API_URL)
    claims_dir = get_claims_data_dir()
    if not claims_dir.is_absolute():
        claims_dir = ROOT / claims_dir
    scope_dir = resolve_claims_scope_dir(claims_dir, args.claim_type)

    print("=" * 60)
    print("同步案件列表并更新 ClaimId")
    print("=" * 60)
    print(f"接口: {api_url}")
    print(f"本地案件目录: {claims_dir}")
    if scope_dir != claims_dir:
        print(f"作用范围(安全删除): {scope_dir}  (claim_type={args.claim_type})")
    else:
        print(f"作用范围(安全删除): {scope_dir}  (未检测到命名空间子目录，按平铺结构处理)")
    print()

    # 1) 拉取接口数据
    try:
        api_list = fetch_claim_list(api_url)
    except Exception as e:
        print(f"请求接口失败: {e}")
        return 1

    if not api_list:
        print("接口返回的案件列表为空，未做任何修改。")
        return 0

    lookup = build_api_lookup(api_list)
    # 用于「保留」判断：本地用 forceid 或 PolicyNo 匹配
    allowed_keys = set(lookup.keys())
    print(f"接口返回 {len(api_list)} 条记录，有效匹配键 {len(allowed_keys)} 个。")

    # 2) 遍历本地案件
    to_keep: List[tuple[Path, str]] = []   # (folder, claim_id)
    to_delete: List[Path] = []

    for folder in iter_claim_folders(scope_dir):
        info = read_claim_info(folder)
        if not info:
            if not args.dry_run:
                to_delete.append(folder)
            else:
                print(f"[dry-run] 将删除（无有效 claim_info）: {folder.name}")
            continue

        forceid = (info.get("forceid") or "").strip()
        policy_no = (info.get("PolicyNo") or "").strip()
        claim_id = lookup.get(forceid) or lookup.get(policy_no)

        if claim_id and (forceid in allowed_keys or policy_no in allowed_keys):
            to_keep.append((folder, claim_id))
        else:
            to_delete.append(folder)

    # 3) 删除不在接口中的案件目录
    if to_delete:
        print(f"\n将删除 {len(to_delete)} 个不在接口列表中的案件目录。")
        for folder in to_delete[:10]:
            print(f"  - {folder.name}")
        if len(to_delete) > 10:
            print(f"  ... 共 {len(to_delete)} 个")
        if not args.dry_run:
            for folder in to_delete:
                import shutil
                try:
                    # 安全护栏：只允许删除 scope_dir 下的内容
                    folder.resolve().relative_to(scope_dir.resolve())
                    shutil.rmtree(folder)
                except Exception as e:
                    print(f"  删除失败 {folder.name}: {e}")
    else:
        print("\n无需删除任何目录。")

    # 4) 为保留的案件写入 ClaimId
    print(f"\n将为 {len(to_keep)} 个案件更新 claim_info.json 中的 ClaimId。")
    updated = 0
    for folder, claim_id in to_keep:
        info_file = folder / "claim_info.json"
        info = read_claim_info(folder)
        if not info:
            continue
        info["ClaimId"] = claim_id
        if not args.dry_run:
            try:
                with open(info_file, "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=2)
                updated += 1
            except Exception as e:
                print(f"  写入失败 {folder.name}: {e}")
        else:
            print(f"  [dry-run] {folder.name} -> ClaimId={claim_id}")
            updated += 1

    print(f"\n已更新 ClaimId 的案件数: {updated}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
