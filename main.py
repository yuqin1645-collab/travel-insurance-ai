#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI理赔审核系统 - 主入口
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

from app.claim_ai_reviewer import main as review_main

if __name__ == "__main__":
    review_main()
