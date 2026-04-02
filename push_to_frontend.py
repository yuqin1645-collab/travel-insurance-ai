#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
推送审核结果到前端API

用法:
  python push_to_frontend.py                    # 批量推送所有审核结果
  python push_to_frontend.py --limit 10         # 只推送前10个
  python push_to_frontend.py --forceid a0nC...  # 推送指定案件
"""

import sys
import asyncio
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from app.output.frontend_pusher import (
    push_from_json_file,
    batch_push_from_directory,
    build_api_payload
)
import json


def find_json_by_forceid(forceid: str) -> Path:
    """根据forceid查找JSON文件"""
    json_dir = Path('review_results/flight_delay')
    json_file = json_dir / f"{forceid}_ai_review.json"

    if json_file.exists():
        return json_file

    # 尝试模糊匹配
    for f in json_dir.glob('*_ai_review.json'):
        if forceid in f.name:
            return f

    return None


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='推送审核结果到前端API')
    parser.add_argument('--forceid', type=str, help='指定案件ID')
    parser.add_argument('--limit', type=int, help='限制推送数量')
    parser.add_argument('--dry-run', action='store_true', help='只打印payload，不实际推送')
    parser.add_argument('--file', type=str, help='指定JSON文件路径')

    args = parser.parse_args()

    if args.dry_run:
        # 只打印payload
        if args.file:
            json_file = Path(args.file)
        elif args.forceid:
            json_file = find_json_by_forceid(args.forceid)
            if not json_file:
                print(f"未找到案件: {args.forceid}")
                return
        else:
            json_file = sorted(Path('review_results/flight_delay').glob('*_ai_review.json'))[-1]

        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        payload = build_api_payload(data)

        print(f"文件: {json_file.name}")
        print()
        print("API Payload:")
        print("=" * 60)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.file:
        # 推送指定文件
        result = await push_from_json_file(Path(args.file))
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.forceid:
        # 推送指定案件
        json_file = find_json_by_forceid(args.forceid)
        if not json_file:
            print(f"未找到案件: {args.forceid}")
            return

        print(f"推送案件: {json_file.name}")
        result = await push_from_json_file(json_file)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        # 批量推送
        results = await batch_push_from_directory(limit=args.limit)

        # 保存结果
        output_file = Path('review_results/_runner/push_results.json')
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        print(f"\n结果已保存: {output_file}")


if __name__ == '__main__':
    asyncio.run(main())