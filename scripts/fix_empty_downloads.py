#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复进度文件中 downloadedFiles 为空但 status=completed 的案件
将其重置为 pending，让下载器重新下载附件
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import config

progress_file = config.CLAIMS_DATA_DIR / ".download_progress.json"

if not progress_file.exists():
    print(f"进度文件不存在: {progress_file}")
    sys.exit(1)

with open(progress_file, "r", encoding="utf-8") as f:
    progress = json.load(f)

print(f"共加载 {len(progress)} 条进度记录")

reset_count = 0
for case_no, record in progress.items():
    status = record.get("status", "")
    downloaded_files = record.get("downloadedFiles", [])
    total_files = record.get("totalFiles", 0)
    file_list = record.get("fileList", [])

    # 条件：status=completed 但 downloadedFiles 为空（附件没有下载）
    # 同时 fileList 不为空（有附件需要下载）
    if status == "completed" and len(downloaded_files) == 0 and len(file_list) > 0:
        # 进一步确认磁盘上确实没有文件（防止误判）
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from app.config import config
        benefit_name = record.get("benefitName", "")
        case_dir = config.CLAIMS_DATA_DIR / benefit_name / f"{benefit_name}-案件号【{case_no}】"
        if not case_dir.exists() or not any(
            f.is_file() and f.name != "claim_info.json"
            for f in case_dir.iterdir()
        ):
            print(f"  重置案件: {case_no} | fileList数量: {len(file_list)}")
            record["downloadedFiles"] = []
            record["failedFiles"] = []
            record["status"] = "pending"
            record["totalFiles"] = len(file_list)
            reset_count += 1
        else:
            print(f"  忽略（磁盘文件已存在）: {case_no}")

print(f"\n共重置 {reset_count} 个案件")

if reset_count > 0:
    # 备份原文件
    backup_file = progress_file.with_suffix(".json.bak")
    with open(backup_file, "w", encoding="utf-8") as f:
        with open(progress_file, "r", encoding="utf-8") as orig:
            f.write(orig.read())
    print(f"原进度文件已备份到: {backup_file}")

    # 写入修改后的进度
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    print(f"进度文件已更新: {progress_file}")
    print("\n现在可以运行下载器重新下载这些案件的附件：")
    print("  python run_incremental.py --no-download  # 重新审核未审核案件")
    print("  或者直接启动: bash start.sh")
else:
    print("没有需要修复的案件")
