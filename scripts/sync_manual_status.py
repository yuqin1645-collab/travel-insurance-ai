#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 Rest_AI_CLaim_Result 接口查询人工处理状态，更新 ai_review_result 表的
benefit_name / manual_status / manual_conclusion 三个字段。

用法:
  venv\\Scripts\\python.exe scripts\\sync_manual_status.py
  venv\\Scripts\\python.exe scripts\\sync_manual_status.py --dry-run
  venv\\Scripts\\python.exe scripts\\sync_manual_status.py --all   # 强制刷新所有记录（含已有值）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

try:
    import pymysql
    import requests
except ImportError as e:
    print(f"缺少依赖: {e}，请先安装: pip install pymysql requests")
    sys.exit(1)

RESULT_API_URL = os.getenv("MANUAL_RESULT_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim_Result")
CLAIMS_DATA_DIR = ROOT / os.getenv("CLAIMS_DATA_DIR", "claims_data")


# ── 数据库 ──────────────────────────────────────────────────────────────────

def get_db_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST", ""),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_forceids(conn, force_all: bool) -> list[dict]:
    """返回需要同步的记录列表，每条含 forceid 和 claim_id"""
    where = "" if force_all else "WHERE manual_status IS NULL"
    with conn.cursor() as cur:
        cur.execute(f"SELECT forceid, claim_id FROM ai_review_result {where}")
        return cur.fetchall()


def update_row(conn, forceid: str, benefit_name: Optional[str],
               manual_status: Optional[str], manual_conclusion: Optional[str],
               dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] {forceid}: benefit_name={benefit_name} "
              f"manual_status={manual_status} manual_conclusion={str(manual_conclusion or '')[:60]}")
        return
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE ai_review_result
               SET benefit_name = %s,
                   manual_status = %s,
                   manual_conclusion = %s,
                   updated_at = CURRENT_TIMESTAMP
               WHERE forceid = %s""",
            (benefit_name, manual_status, manual_conclusion, forceid),
        )
    conn.commit()


# ── claim_info.json ─────────────────────────────────────────────────────────

_forceid_info_cache: dict[str, dict] = {}


def _build_cache():
    if _forceid_info_cache:
        return
    for f in CLAIMS_DATA_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            fid = str(data.get("forceid") or "").strip()
            if fid:
                _forceid_info_cache[fid] = data
        except Exception:
            pass


def get_benefit_name(forceid: str) -> Optional[str]:
    _build_cache()
    info = _forceid_info_cache.get(forceid, {})
    return (info.get("BenefitName") or info.get("benefit_name") or None)


# ── 接口查询 ─────────────────────────────────────────────────────────────────

def query_manual_results_batch(forceids: list, timeout: int = 30) -> dict:
    """
    批量查询人工处理状态（支持分页）。
    POST {"pageSize":"100","pageIndex":"1","data":[forceid1, forceid2, ...]}
    接口返回: {"totalPage": N, "totalCount": N, "data": [...]}
    返回 {forceid: result_dict} 映射。
    """
    result_map = {}
    try:
        page = 1
        total_page = 1
        while page <= total_page:
            resp = requests.post(
                RESULT_API_URL,
                json={"pageSize": "100", "pageIndex": str(page), "data": forceids},
                timeout=timeout,
            )
            resp.raise_for_status()
            raw = resp.json()

            if isinstance(raw, list):
                items = raw
                total_page = 1
            elif isinstance(raw, dict):
                total_page = int(raw.get("totalPage", 1))
                total_count = raw.get("totalCount", 0)
                if page == 1:
                    print(f"  接口返回: totalCount={total_count}, totalPage={total_page}")
                inner = raw.get("data")
                items = inner if isinstance(inner, list) else [raw]
            else:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                fid = str(item.get("forceid") or item.get("ForceId") or "").strip()
                if fid:
                    result_map[fid] = item

            if page >= total_page:
                break
            page += 1
            time.sleep(0.5)

        return result_map
    except Exception as e:
        print(f"  [警告] 批量查询接口失败: {e}")
        return result_map if result_map else {}


# ── 状态映射 ─────────────────────────────────────────────────────────────────

def map_manual_status(result: dict) -> tuple[Optional[str], Optional[str]]:
    """
    根据接口返回的 Final_Status 字段判断人工状态：
      - 事后理赔拒赔         → 拒绝，结论取 Assessment_Remark
      - 待补件               → 需补齐资料，结论取 Supplementary_Reason
      - 支付成功             → 通过，结论取 Approved_amount
      - 其他中间状态（线上理赔初审等）→ 通过（人工尚未最终审核，先按通过计，后续覆盖）
    """
    final_status = str(result.get("Final_Status") or "").strip()
    supplementary_reason = str(result.get("Supplementary_Reason") or "").strip()
    approved = str(result.get("Approved_amount") or "").strip()

    if final_status == "事后理赔拒赔":
        return "拒绝", str(result.get("Assessment_Remark") or "").strip()
    if final_status == "待补件":
        return "需补齐资料", supplementary_reason
    if final_status == "支付成功":
        return "通过", approved

    # 非最终状态（如线上理赔初审），标记为待定，前端过滤
    return "待定", final_status


# ── 主流程 ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="同步人工处理状态到 ai_review_result")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入数据库")
    parser.add_argument("--all", dest="force_all", action="store_true",
                        help="强制刷新所有记录（默认只处理 manual_status IS NULL）")
    parser.add_argument("--sleep", type=float, default=0.3,
                        help="每条请求间隔秒数，默认 0.3")
    args = parser.parse_args()

    conn = get_db_conn()
    try:
        rows = fetch_forceids(conn, args.force_all)
    except Exception as e:
        print(f"查询数据库失败: {e}")
        conn.close()
        return 1

    print(f"共 {len(rows)} 条记录待同步")
    if not rows:
        conn.close()
        return 0

    success = fail = skip = 0
    forceids = [row["forceid"] for row in rows]

    # 批量查询接口
    print(f"批量查询接口，共 {len(forceids)} 个 forceid...")
    result_map = query_manual_results_batch(forceids)
    print(f"接口返回 {len(result_map)} 条结果")

    for row in rows:
        forceid = row["forceid"]

        # benefit_name 从本地 claim_info.json 读取
        benefit_name = get_benefit_name(forceid)

        result = result_map.get(forceid)
        if result is None:
            skip += 1
            print(f"  [跳过] {forceid}: 接口无返回")
            if benefit_name:
                update_row(conn, forceid, benefit_name, None, None, args.dry_run)
            continue

        manual_status, manual_conclusion = map_manual_status(result)

        try:
            update_row(conn, forceid, benefit_name, manual_status, manual_conclusion, args.dry_run)
            success += 1
            print(f"  OK {forceid}: status={manual_status} benefit={benefit_name}")
        except Exception as e:
            fail += 1
            print(f"  FAIL {forceid}: 写入失败 {e}")

    conn.close()

    print(f"\n完成：成功 {success}，跳过 {skip}，失败 {fail}")
    if args.dry_run:
        print("(dry-run 模式，未实际写入)")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
