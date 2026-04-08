#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 review_results 下的 AI 审核结果同步到数据库（用于 nlpp 等上游系统）

用法:
  venv\\Scripts\\python.exe scripts\\sync_review_to_db.py
  venv\\Scripts\\python.exe scripts\\sync_review_to_db.py --dry-run   # 仅打印，不写入
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def get_db_config() -> dict:
    host = os.getenv("DB_HOST", "")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", "")
    password = os.getenv("DB_PASSWORD", "")
    database = os.getenv("DB_NAME", "ai")
    if not all([host, user, password]):
        raise RuntimeError(
            "请在 .env 中配置 DB_HOST, DB_USER, DB_PASSWORD (DB_NAME 默认 ai)"
        )
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
    }


def load_review_results(results_dir: Path) -> list[dict]:
    """加载 review_results 下所有 *_ai_review.json"""
    out = []
    for f in results_dir.rglob("*_ai_review.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("forceid"):
                out.append(data)
        except Exception as e:
            print(f"  跳过 {f.name}: {e}")
    return out


def ensure_table(conn) -> None:
    """若表不存在则创建；若已存在则确保有 claim_id 列（兼容旧表）"""
    sql = """
    CREATE TABLE IF NOT EXISTS ai_review_result (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        forceid VARCHAR(64) NOT NULL COMMENT '案件唯一ID',
        claim_id VARCHAR(64) NULL COMMENT 'ClaimId(与上游一致)',
        remark VARCHAR(2000) NOT NULL DEFAULT '' COMMENT '审核结论摘要',
        is_additional CHAR(1) NOT NULL DEFAULT 'Y' COMMENT 'Y=需补件, N=最终结论',
        key_conclusions LONGTEXT COMMENT '各核对点结论(JSON)',
        raw_result LONGTEXT COMMENT '完整原始JSON',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uk_forceid (forceid),
        KEY idx_is_additional (is_additional),
        KEY idx_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI理赔审核结果'
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        try:
            cur.execute(
                "ALTER TABLE ai_review_result ADD COLUMN claim_id VARCHAR(64) NULL "
                "COMMENT 'ClaimId(与上游一致)' AFTER forceid"
            )
        except Exception as e:
            if "Duplicate column" not in str(e):
                raise
    conn.commit()


def get_claim_id_for_forceid(forceid: str, claims_dir: Path) -> str | None:
    """从 claims_data 下该案件的 claim_info.json 读取 ClaimId"""
    for info_file in claims_dir.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("forceid") == forceid:
            return (data.get("ClaimId") or data.get("claimId")) or None
    return None


def sync_to_db(results: list[dict], dry_run: bool = False) -> tuple[int, int]:
    """同步到数据库，返回 (成功数, 失败数)"""
    import pymysql

    cfg = get_db_config()
    success = 0
    fail = 0

    if dry_run:
        print(f"[dry-run] 将同步 {len(results)} 条记录到 {cfg['host']}/{cfg['database']}")
        for r in results[:3]:
            print(f"  示例: forceid={r.get('forceid')} remark={str(r.get('Remark', ''))[:60]}...")
        return len(results), 0

    claims_dir = ROOT / os.getenv("CLAIMS_DATA_DIR", "claims_data")
    if not claims_dir.is_absolute():
        claims_dir = ROOT / claims_dir

    conn = pymysql.connect(**cfg)
    try:
        ensure_table(conn)
        with conn.cursor() as cur:
            sql = """
                INSERT INTO ai_review_result (forceid, claim_id, remark, is_additional, key_conclusions, raw_result)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    claim_id = VALUES(claim_id),
                    remark = VALUES(remark),
                    is_additional = VALUES(is_additional),
                    key_conclusions = VALUES(key_conclusions),
                    raw_result = VALUES(raw_result),
                    updated_at = CURRENT_TIMESTAMP
            """
            for r in results:
                try:
                    forceid = r.get("forceid", "")
                    claim_id = get_claim_id_for_forceid(forceid, claims_dir)
                    remark = r.get("Remark", "")[:2000]
                    is_additional = str(r.get("IsAdditional", "Y"))[:1]
                    key_conclusions = json.dumps(r.get("KeyConclusions", []), ensure_ascii=False)
                    raw_result = json.dumps(r, ensure_ascii=False)
                    cur.execute(sql, (forceid, claim_id, remark, is_additional, key_conclusions, raw_result))
                    success += 1
                except Exception as e:
                    fail += 1
                    print(f"  写入失败 forceid={r.get('forceid')}: {e}")
        conn.commit()
    finally:
        conn.close()

    return success, fail


def sync_review_to_db_for_forceid(review_result: dict) -> bool:
    """
    根据审核结果 dict 同步到数据库（覆盖写入）。
    用于 rerun_redownloaded.py 在单个案件重审后调用。
    返回 True 表示成功，False 表示失败。
    """
    try:
        success, fail = sync_to_db([review_result], dry_run=False)
        return fail == 0
    except Exception as e:
        print(f"[DB同步异常] forceid={review_result.get('forceid')}: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="同步AI审核结果到数据库")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    args = parser.parse_args()

    results_dir = Path(os.getenv("REVIEW_RESULTS_DIR", "review_results"))
    if not ROOT.joinpath(results_dir).exists():
        results_dir = ROOT / "review_results"
    else:
        results_dir = ROOT / results_dir

    print("=" * 60)
    print("同步 AI 审核结果到数据库")
    print("=" * 60)
    print(f"结果目录: {results_dir}")

    results = load_review_results(results_dir)
    print(f"找到 {len(results)} 条审核结果")

    if not results:
        print("无数据可同步")
        return 0

    try:
        success, fail = sync_to_db(results, dry_run=args.dry_run)
        print(f"\n成功: {success}, 失败: {fail}")
        if args.dry_run:
            print("(dry-run 模式，未实际写入)")
        return 0 if fail == 0 else 1
    except Exception as e:
        print(f"同步失败: {e}")
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
