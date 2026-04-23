#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从数据库 ai_claim_info_raw 恢复 claims_data 目录结构并重新下载材料文件。

用法:
  python scripts/restore_claims_from_db.py              # 恢复全部
  python scripts/restore_claims_from_db.py --dry-run    # 只打印，不下载
  python scripts/restore_claims_from_db.py --skip-existing  # 跳过已存在的目录（增量补充）
"""

import os
import sys
import json
import time
import argparse
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse, unquote

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import pymysql
from app.config import config

CLAIMS_DIR = config.CLAIMS_DATA_DIR

# 险种名 → claims_data 子目录名
BENEFIT_DIR_MAP = {
    "行李延误": "行李延误",
    "航班延误": "航班延误",
}


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


def guess_ext(url: str, content_type: str = "") -> str:
    """从 URL 或 Content-Type 推断文件扩展名"""
    path = unquote(urlparse(url).path)
    ext = Path(path).suffix.lower()
    if ext in (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"):
        return ext
    ct = content_type.lower()
    if "pdf" in ct:
        return ".pdf"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    return ext or ".bin"


def download_file(url: str, dest: Path, idx: int) -> bool:
    """下载单个文件，返回是否成功"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            ct = resp.headers.get("Content-Type", "")
            ext = guess_ext(url, ct)
            filename = dest / f"file_{idx:03d}{ext}"
            filename.write_bytes(resp.read())
        return True
    except Exception as e:
        print(f"      [下载失败] {e}")
        return False


def make_claim_dir(benefit_name: str, policy_no: str) -> Path:
    """构造案件目录路径，与原始下载脚本保持一致：{险种}-案件号【{PolicyNo}】"""
    sub = BENEFIT_DIR_MAP.get(benefit_name, benefit_name)
    base = CLAIMS_DIR / sub
    dir_name = f"{sub}-案件号【{policy_no}】"
    return base / dir_name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印，不实际下载")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已存在目录，只补充缺失案件")
    args = parser.parse_args()

    conn = get_db_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT forceid, benefit_name, claim_id, raw_json FROM ai_claim_info_raw ORDER BY benefit_name, forceid")
        rows = cur.fetchall()
    conn.close()

    print(f"数据库共 {len(rows)} 条记录")

    ok = skip = fail_download = 0

    for row in rows:
        forceid = row["forceid"]
        benefit = row["benefit_name"] or "未知险种"
        claim_id = row["claim_id"] or forceid

        try:
            raw = json.loads(row["raw_json"])
        except Exception:
            print(f"[跳过] {forceid} raw_json 解析失败")
            continue

        policy_no = raw.get("PolicyNo") or claim_id
        claim_dir = make_claim_dir(benefit, policy_no)

        if args.skip_existing and claim_dir.exists():
            skip += 1
            continue

        print(f"\n[{ok+skip+fail_download+1}/{len(rows)}] {forceid}  {benefit}  PolicyNo={policy_no}")
        print(f"  目录: {claim_dir}")

        if args.dry_run:
            file_list = raw.get("FileList", [])
            print(f"  文件数: {len(file_list)} (dry-run，跳过下载)")
            ok += 1
            continue

        # 创建目录
        claim_dir.mkdir(parents=True, exist_ok=True)

        # 写入 claim_info.json（去掉 FileList，与原始格式一致）
        info = {k: v for k, v in raw.items() if k != "FileList"}
        (claim_dir / "claim_info.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 下载材料文件
        file_list = raw.get("FileList", [])
        dl_ok = dl_fail = 0
        for i, f in enumerate(file_list, 1):
            url = f.get("FileUrl", "")
            if not url:
                continue
            success = download_file(url, claim_dir, i)
            if success:
                dl_ok += 1
            else:
                dl_fail += 1
            time.sleep(0.1)  # 避免请求过快

        print(f"  文件下载: {dl_ok} 成功 / {dl_fail} 失败")
        if dl_fail > 0:
            fail_download += 1
        else:
            ok += 1

    print(f"\n{'='*50}")
    print(f"恢复完成: {ok} 成功 / {skip} 跳过(已存在) / {fail_download} 部分下载失败")


if __name__ == "__main__":
    main()
