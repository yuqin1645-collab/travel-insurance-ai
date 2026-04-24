#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一报表脚本：导出航班延误/行李延误/对比报表

用法:
  python report.py --type flight       # 导出航班延误Excel报表
  python report.py --type baggage      # 导出行李延误Excel报表
  python report.py --type compare      # 导出AI vs 人工对比报表
"""

import sys
import os
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()


def cmd_flight():
    """导出航班延误AI审核报表"""
    from scripts.export_flight_delay_ai_report import main as flight_main
    flight_main()


def cmd_baggage():
    """生成行李延误审核报表"""
    from scripts.generate_baggage_report import main as baggage_main
    baggage_main()


def cmd_compare():
    """导出AI vs 人工审核对比报表"""
    from scripts.export_ai_vs_manual_report import main as compare_main
    compare_main()


def main():
    parser = argparse.ArgumentParser(description="统一报表脚本")
    parser.add_argument("--type", required=True, choices=["flight", "baggage", "compare"],
                        help="报表类型")
    args = parser.parse_args()

    if args.type == "flight":
        cmd_flight()
    elif args.type == "baggage":
        cmd_baggage()
    elif args.type == "compare":
        cmd_compare()


if __name__ == "__main__":
    main()
