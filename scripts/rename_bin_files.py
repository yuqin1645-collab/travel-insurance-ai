#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量重命名 claims_data 下的 .bin 文件为正确扩展名。
按文件头魔数检测真实类型：JPEG→.jpg, PNG→.png, PDF→.pdf
"""

import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_DIR = ROOT / "claims_data"

MAGIC_BYTES = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"%PDF": ".pdf",
}

renamed = Counter()
skipped = Counter()

for bin_file in sorted(CLAIMS_DIR.rglob("*.bin")):
    try:
        with open(bin_file, "rb") as f:
            data = f.read(16)
    except Exception as e:
        skipped["read_error"] += 1
        print(f"  读取失败: {bin_file.name}: {e}")
        continue

    new_ext = None
    for magic, ext in MAGIC_BYTES.items():
        if data[:len(magic)] == magic:
            new_ext = ext
            break

    if new_ext is None:
        skipped["unrecognized"] += 1
        continue

    new_path = bin_file.with_suffix(new_ext)

    # 如果目标已存在，跳过（可能已处理过）
    if new_path.exists():
        skipped["target_exists"] += 1
        continue

    bin_file.rename(new_path)
    renamed[new_ext] += 1
    print(f"  {bin_file.name} → {new_path.name}")

total_renamed = sum(renamed.values())
total_skipped = sum(skipped.values())
print(f"\n{'='*40}")
print(f"完成:")
for ext, cnt in renamed.most_common():
    print(f"  重命名为 {ext}: {cnt}")
print(f"  跳过（无匹配魔数/目标已存在）: {total_skipped}")
print(f"  总计: {total_renamed} 个文件已重命名")
