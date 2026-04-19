#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
配置管理模块
从.env文件加载配置
"""

import os
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()


class Config:
    """配置类"""

    # DashScope (Qwen) API配置
    DASHSCOPE_API_KEY: str = os.getenv('DASHSCOPE_API_KEY', os.getenv('OPENROUTER_API_KEY', ''))
    DASHSCOPE_BASE_URL: str = os.getenv('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1')

    # OpenRouter API配置 (兼容旧配置)
    OPENROUTER_API_KEY: str = os.getenv('OPENROUTER_API_KEY', '')
    OPENROUTER_BASE_URL: str = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')

    # 模型配置 - Qwen系列
    MODEL_VISION: str = os.getenv('MODEL_VISION', 'qwen-vl-plus')   # 视觉模型
    MODEL_VISION_PROVIDER: str = os.getenv('MODEL_VISION_PROVIDER', 'dashscope')  # 视觉模型提供商: dashscope 或 openrouter
    USE_QWEN_VISION: bool = os.getenv('USE_QWEN_VISION', 'true').lower() != 'false'  # true=Qwen, false=OpenRouter/Gemini
    MODEL_VISION_OPENROUTER: str = os.getenv('MODEL_VISION_OPENROUTER', 'google/gemini-2.5-pro-preview')  # OpenRouter视觉模型
    MODEL_SIMPLE: str = os.getenv('MODEL_SIMPLE', 'qwen-plus')       # 简单任务
    MODEL_MEDIUM: str = os.getenv('MODEL_MEDIUM', 'qwen-plus')       # 中等任务
    MODEL_HARD: str = os.getenv('MODEL_HARD', 'qwen-plus')           # 困难任务
    MODEL_EXPERT: str = os.getenv('MODEL_EXPERT', 'qwen-plus')        # 专家任务

    # 模型参数
    TEMPERATURE: float = float(os.getenv('TEMPERATURE', '0.1'))
    TIMEOUT: int = int(os.getenv('TIMEOUT', '60'))

    # 航班数据源配置
    FLIGHT_DATA_PROVIDER: str = os.getenv('FLIGHT_DATA_PROVIDER', 'mock')
    AVIATIONSTACK_API_KEY: str = os.getenv('AVIATIONSTACK_API_KEY', '')

    # 材料齐全检查策略
    # - ocr_text: 使用OCR/文档提取的文本(可能触发prompt过长)
    # - vision:   将材料(图片/PDF页面)作为多模态输入交给模型阅读(更抗OCR误差/更不易400)
    # 经过验证：vision + 分批看图更稳定，默认启用
    MATERIAL_CHECK_MODE: str = os.getenv('MATERIAL_CHECK_MODE', 'vision')
    # vision 模式下: 每个PDF最多抽取前N页转成图片发送(控制请求体积)
    VISION_PDF_MAX_PAGES: int = int(os.getenv('VISION_PDF_MAX_PAGES', '2'))
    # vision 模式下: 图片最长边像素(会等比缩放)
    VISION_IMAGE_MAX_EDGE: int = int(os.getenv('VISION_IMAGE_MAX_EDGE', '1800'))
    # vision 模式下: JPEG质量(1-95)
    VISION_IMAGE_JPEG_QUALITY: int = int(os.getenv('VISION_IMAGE_JPEG_QUALITY', '82'))
    # vision 模式下: 单次请求最多发送多少张附件图片（避免模型/通道对图片数量限制或注意力稀释）
    VISION_MAX_ATTACHMENTS: int = int(os.getenv('VISION_MAX_ATTACHMENTS', '10'))
    VISION_GLOBAL_CONCURRENCY: int = int(os.getenv('VISION_GLOBAL_CONCURRENCY', '6'))
    VISION_RETRY_NETWORK_MAX_ATTEMPTS: int = int(os.getenv('VISION_RETRY_NETWORK_MAX_ATTEMPTS', '3'))
    VISION_RETRY_JSON_MAX_ATTEMPTS: int = int(os.getenv('VISION_RETRY_JSON_MAX_ATTEMPTS', '2'))
    VISION_RETRY_BASE_DELAY: float = float(os.getenv('VISION_RETRY_BASE_DELAY', '2.0'))
    VISION_RETRY_MAX_DELAY: float = float(os.getenv('VISION_RETRY_MAX_DELAY', '20.0'))
    VISION_RETRY_JITTER: float = float(os.getenv('VISION_RETRY_JITTER', '0.35'))
    
    # OCR配置
    OCR_PROVIDER: str = os.getenv('OCR_PROVIDER', 'tesseract')
    OCR_API_KEY: str = os.getenv('OCR_API_KEY', '')
    OCR_API_SECRET: str = os.getenv('OCR_API_SECRET', '')
    TESSERACT_PATH: str = os.getenv('TESSERACT_PATH', r'/usr/bin/tesseract')
    
    # OCR缓存配置
    OCR_CACHE_ENABLED: bool = os.getenv('OCR_CACHE_ENABLED', 'true').lower() == 'true'
    OCR_CACHE_DIR: Path = Path(os.getenv('OCR_CACHE_DIR', '.cache/ocr'))
    OCR_CACHE_EXPIRE_DAYS: int = int(os.getenv('OCR_CACHE_EXPIRE_DAYS', '30'))
    
    # 文档处理缓存配置
    DOC_CACHE_ENABLED: bool = os.getenv('DOC_CACHE_ENABLED', 'true').lower() == 'true'
    DOC_CACHE_DIR: Path = Path(os.getenv('DOC_CACHE_DIR', '.cache/docs'))
    DOC_CACHE_EXPIRE_DAYS: int = int(os.getenv('DOC_CACHE_EXPIRE_DAYS', '30'))

    # 启用的险种类型（逗号分隔，默认只开航班延误）
    # 生产环境：ENABLED_CLAIM_TYPES=flight_delay
    # 全量开启：ENABLED_CLAIM_TYPES=flight_delay,baggage_delay
    ENABLED_CLAIM_TYPES: List[str] = [
        t.strip() for t in os.getenv('ENABLED_CLAIM_TYPES', 'flight_delay').split(',') if t.strip()
    ]
    
    # 审核配置
    MAX_RETRY: int = int(os.getenv('MAX_RETRY', '3'))
    SINGLE_ITEM_LIMIT: float = float(os.getenv('SINGLE_ITEM_LIMIT', '1000'))
    DEPRECIATION_RATE: float = float(os.getenv('DEPRECIATION_RATE', '0.01'))
    
    # 数据库配置
    DB_HOST: str = os.getenv('DB_HOST', 'localhost')
    DB_PORT: int = int(os.getenv('DB_PORT', '3306'))
    DB_USER: str = os.getenv('DB_USER', 'root')
    DB_PASSWORD: str = os.getenv('DB_PASSWORD', '')
    DB_NAME: str = os.getenv('DB_NAME', 'ai')

    # 生产化调度配置
    DOWNLOAD_INTERVAL: int = int(os.getenv('DOWNLOAD_INTERVAL', '3600'))  # 1小时
    REVIEW_INTERVAL: int = int(os.getenv('REVIEW_INTERVAL', '600'))  # 10分钟
    SUPPLEMENTARY_CHECK_INTERVAL: int = int(os.getenv('SUPPLEMENTARY_CHECK_INTERVAL', '1800'))  # 30分钟
    CLEANUP_INTERVAL: int = int(os.getenv('CLEANUP_INTERVAL', '86400'))  # 24小时

    # 重试配置
    MAX_DOWNLOAD_RETRIES: int = int(os.getenv('MAX_DOWNLOAD_RETRIES', '3'))
    MAX_REVIEW_RETRIES: int = int(os.getenv('MAX_REVIEW_RETRIES', '2'))
    RETRY_BACKOFF_BASE: int = int(os.getenv('RETRY_BACKOFF_BASE', '2'))

    # 补件配置
    SUPPLEMENTARY_DEADLINE_HOURS: int = int(os.getenv('SUPPLEMENTARY_DEADLINE_HOURS', '24'))
    MAX_SUPPLEMENTARY_COUNT: int = int(os.getenv('MAX_SUPPLEMENTARY_COUNT', '3'))
    SUPPLEMENTARY_REMINDER_HOURS: int = int(os.getenv('SUPPLEMENTARY_REMINDER_HOURS', '6'))

    # 前端API配置
    FRONTEND_API_URL: str = os.getenv('FRONTEND_API_URL', '')
    FRONTEND_API_KEY: str = os.getenv('FRONTEND_API_KEY', '')
    FRONTEND_TIMEOUT: int = int(os.getenv('FRONTEND_TIMEOUT', '30'))

    # 监控告警配置
    ALERT_EMAIL: str = os.getenv('ALERT_EMAIL', '')
    SLACK_WEBHOOK_URL: str = os.getenv('SLACK_WEBHOOK_URL', '')
    SENTRY_DSN: str = os.getenv('SENTRY_DSN', '')

    # 文件路径配置
    POLICY_TERMS_DIR: Path = Path(os.getenv('POLICY_TERMS_DIR', '旅行险条款'))
    MATERIAL_CHECKLIST_URL: str = os.getenv('MATERIAL_CHECKLIST_URL', 'https://www.kdocs.cn/l/ch8Qrro1EkYQ')
    CLAIMS_DATA_DIR: Path = Path(os.getenv('CLAIMS_DATA_DIR', 'claims_data'))
    REVIEW_RESULTS_DIR: Path = Path(os.getenv('REVIEW_RESULTS_DIR', 'review_results'))
    PROMPTS_DIR: Path = Path(os.getenv('PROMPTS_DIR', 'prompts'))

    # 生产化文件路径
    PRODUCTION_DIR: Path = Path(os.getenv('PRODUCTION_DIR', 'app/production'))
    SCHEDULER_DIR: Path = Path(os.getenv('SCHEDULER_DIR', 'app/scheduler'))
    SUPPLEMENTARY_DIR: Path = Path(os.getenv('SUPPLEMENTARY_DIR', 'app/supplementary'))
    OUTPUT_DIR: Path = Path(os.getenv('OUTPUT_DIR', 'app/output'))
    STATE_DIR: Path = Path(os.getenv('STATE_DIR', 'app/state'))
    MONITORING_DIR: Path = Path(os.getenv('MONITORING_DIR', 'app/monitoring'))
    ERROR_DIR: Path = Path(os.getenv('ERROR_DIR', 'app/error'))
    
    @classmethod
    def validate(cls) -> bool:
        """验证必需的配置是否存在"""
        if not cls.OPENROUTER_API_KEY:
            print("错误: 未设置OPENROUTER_API_KEY")
            print("请在.env文件中设置或使用环境变量")
            return False
        return True
    
    @classmethod
    def get_model_by_difficulty(cls, difficulty: str) -> str:
        """根据难度获取模型"""
        mapping = {
            'simple': cls.MODEL_SIMPLE,
            'medium': cls.MODEL_MEDIUM,
            'hard': cls.MODEL_HARD,
            'expert': cls.MODEL_EXPERT,
        }
        return mapping.get(difficulty.lower(), cls.MODEL_MEDIUM)
    
    @classmethod
    def to_dict(cls) -> Dict:
        """转换为字典"""
        return {
            'openrouter_api_key': '***' if cls.OPENROUTER_API_KEY else '',
            'openrouter_base_url': cls.OPENROUTER_BASE_URL,
            'model_simple': cls.MODEL_SIMPLE,
            'model_medium': cls.MODEL_MEDIUM,
            'model_hard': cls.MODEL_HARD,
            'model_expert': cls.MODEL_EXPERT,
            'temperature': cls.TEMPERATURE,
            'timeout': cls.TIMEOUT,
            'single_item_limit': cls.SINGLE_ITEM_LIMIT,
            'depreciation_rate': cls.DEPRECIATION_RATE,
            'db_host': cls.DB_HOST,
            'db_port': cls.DB_PORT,
            'db_name': cls.DB_NAME,
            'download_interval': cls.DOWNLOAD_INTERVAL,
            'review_interval': cls.REVIEW_INTERVAL,
            'supplementary_check_interval': cls.SUPPLEMENTARY_CHECK_INTERVAL,
            'max_supplementary_count': cls.MAX_SUPPLEMENTARY_COUNT,
            'frontend_api_url': cls.FRONTEND_API_URL,
        }
    
    @classmethod
    def print_config(cls):
        """打印配置信息"""
        print("=" * 60)
        print("当前配置:")
        print("=" * 60)
        for key, value in cls.to_dict().items():
            print(f"{key}: {value}")
        print("=" * 60)


# 创建全局配置实例
config = Config()


if __name__ == "__main__":
    # 测试配置
    config.print_config()
    
    if config.validate():
        print("\n✓ 配置验证通过")
    else:
        print("\n✗ 配置验证失败")
