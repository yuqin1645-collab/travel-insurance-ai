#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一数据管理脚本：同步API / 恢复数据 / 下载材料

注意：download_claims.py 保持不变，作为核心库被 scheduler 等模块调用。
此脚本提供统一 CLI 入口。

用法:
  python data.py sync                     # 从API同步案件列表
  python data.py sync --no-delete         # 只新增不删除
  python data.py sync --dry-run           # 预览不写入
  python data.py restore                  # 从数据库恢复 claims_data
  python data.py restore --skip-existing  # 跳过已存在的
  python data.py download                 # 全量下载理赔材料
"""

import sys
import os
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()


def cmd_sync(no_delete: bool = False, dry_run: bool = False):
    """从API同步案件列表"""
    cmd = [
        sys.executable, str(ROOT / "scripts" / "sync_claims_from_api.py"),
    ]
    if no_delete:
        cmd.append("--no-delete")
    if dry_run:
        cmd.append("--dry-run")

    import subprocess
    result = subprocess.run(cmd, cwd=ROOT)
    sys.exit(result.returncode)


def cmd_restore(skip_existing: bool = False, dry_run: bool = False):
    """从数据库恢复 claims_data"""
    cmd = [
        sys.executable, str(ROOT / "scripts" / "restore_claims_from_db.py"),
    ]
    if skip_existing:
        cmd.append("--skip-existing")
    if dry_run:
        cmd.append("--dry-run")

    import subprocess
    result = subprocess.run(cmd, cwd=ROOT)
    sys.exit(result.returncode)


def cmd_download():
    """全量下载理赔材料"""
    cmd = [
        sys.executable, str(ROOT / "scripts" / "download_claims.py"),
    ]

    import subprocess
    result = subprocess.run(cmd, cwd=ROOT)
    sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="统一数据管理脚本")
    sub = parser.add_subparsers(dest="action", required=True)

    sync_p = sub.add_parser("sync", help="从API同步案件列表")
    sync_p.add_argument("--no-delete", action="store_true", help="只新增不删除")
    sync_p.add_argument("--dry-run", action="store_true", help="预览")

    restore_p = sub.add_parser("restore", help="从数据库恢复claims_data")
    restore_p.add_argument("--skip-existing", action="store_true", help="跳过已存在")
    restore_p.add_argument("--dry-run", action="store_true", help="预览")

    sub.add_parser("download", help="全量下载理赔材料")

    args = parser.parse_args()

    if args.action == "sync":
        cmd_sync(args.no_delete, args.dry_run)
    elif args.action == "restore":
        cmd_restore(args.skip_existing, args.dry_run)
    elif args.action == "download":
        cmd_download()


if __name__ == "__main__":
    main()
