#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI理赔审核器 — 纯服务类，负责初始化 client/vision/OCR 等能力。
路由、批量运行、单案件编排已提取到：
  - app/modules/router.py         detect_claim_type
  - app/runner.py                 review_claim_async / main_async / main
  - app/modules/*/stages/ai_calls.py   各险种 AI 调用独立函数
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp

from app.config import config
from app.document_processor import DocumentProcessor
from app.gemini_vision_client import GeminiVisionClient
from app.ocr_service import OCRService
from app.openrouter_client import OpenRouterClient
from app.privacy_masking import PrivacyMasker
from app.prompt_loader import prompt_loader
from app.modules.registry import DEFAULT_REGISTRY
from app.modules.router import detect_claim_type
from app.logging_utils import LOGGER, log_extra as _log_extra

# 薄包装委托到 ai_calls 独立函数，保持 pipeline 调用方兼容
from app.modules.baggage_delay.stages.ai_calls import (
    run_baggage_delay_audit,
    run_baggage_delay_parse,
    run_pir_receipt_time_extract,
)
from app.modules.flight_delay.stages.ai_calls import (
    run_flight_delay_audit,
    run_flight_delay_parse,
    run_flight_delay_vision_extract,
)


class AIClaimReviewer:
    """AI理赔审核器 - 使用 DashScope (Qwen) API"""

    def __init__(self, api_key: Optional[str] = None):
        self.client = OpenRouterClient(api_key=api_key)
        self.prompt_loader = prompt_loader
        self.privacy_masker = PrivacyMasker()
        self.doc_processor = DocumentProcessor()
        self.vision_client = GeminiVisionClient(api_key=config.DASHSCOPE_API_KEY)
        self.module = DEFAULT_REGISTRY.get("baggage_damage")
        self.prompt_namespace = self.module.get_context().prompt_namespace
        self.ocr_service = OCRService(cache_namespace=self.module.get_context().claim_type)

    # ── 模块上下文切换 ──────────────────────────────────────────────────────

    def set_claim_type(self, claim_type: str) -> None:
        self.module = DEFAULT_REGISTRY.get(claim_type)
        ctx = self.module.get_context()
        self.prompt_namespace = ctx.prompt_namespace
        self.ocr_service = OCRService(cache_namespace=ctx.claim_type)

    # ── AI 调用薄包装 — 委托到 ai_calls 独立函数，保持 pipeline 调用兼容 ──

    async def _ai_flight_delay_parse_async(
        self, claim_info: Dict, free_text: str, session: aiohttp.ClientSession,
    ) -> Dict:
        return await run_flight_delay_parse(
            self.client, self.prompt_loader, self.prompt_namespace,
            claim_info, free_text, session,
        )

    async def _ai_flight_delay_vision_extract_async(
        self, attachment_paths: list, claim_info: Dict, session: aiohttp.ClientSession,
    ) -> Dict:
        return await run_flight_delay_vision_extract(
            self.vision_client, self.prompt_loader, self.prompt_namespace,
            attachment_paths, claim_info, session,
        )

    async def _ai_flight_delay_audit_async(
        self,
        claim_info: Dict,
        parsed: Dict,
        policy_terms_excerpt: str,
        session: aiohttp.ClientSession,
        payout_json: Optional[Dict] = None,
    ) -> Dict:
        return await run_flight_delay_audit(
            self.client, self.prompt_loader, self.prompt_namespace,
            claim_info, parsed, policy_terms_excerpt, session, payout_json,
        )

    async def _ai_baggage_delay_parse_async(
        self, claim_info: Dict, free_text: str, session: aiohttp.ClientSession,
    ) -> Dict:
        return await run_baggage_delay_parse(
            self.client, self.prompt_loader, self.prompt_namespace,
            claim_info, free_text, session,
        )

    async def _ai_baggage_delay_audit_async(
        self,
        claim_info: Dict,
        parsed: Dict,
        policy_terms_excerpt: str,
        session: aiohttp.ClientSession,
    ) -> Dict:
        return await run_baggage_delay_audit(
            self.client, self.prompt_loader, self.prompt_namespace,
            claim_info, parsed, policy_terms_excerpt, session,
        )

    async def _ai_pir_receipt_time_extract_async(
        self, attachment_paths: List[Path], claim_info: Dict, session: aiohttp.ClientSession,
    ) -> Dict:
        return await run_pir_receipt_time_extract(
            self.vision_client, self.prompt_loader, self.prompt_namespace,
            attachment_paths, claim_info, session,
        )

    # ── 案件加载 ────────────────────────────────────────────────────────────

    def _load_claim_info(self, claim_folder: Path) -> Dict:
        claim_info_file = claim_folder / "claim_info.json"
        with open(claim_info_file, "r", encoding="utf-8") as f:
            return json.load(f)

    # ── OCR 材料处理 ────────────────────────────────────────────────────────

    def _ocr_all_materials(self, claim_folder: Path) -> Dict:
        """处理所有材料文件(图片、PDF、DOCX等)。支持OCR识别和文档提取。"""
        files = list(claim_folder.glob("*"))
        material_files = [
            f for f in files
            if f.is_file()
            and f.name != "claim_info.json"
            and f.suffix.lower() in [".jpg", ".jpeg", ".png", ".pdf", ".docx", ".doc"]
        ]

        if not material_files:
            return {}

        all_results = {}
        for file in material_files:
            suffix = file.suffix.lower()
            if suffix in [".pdf", ".docx", ".doc"]:
                result = self.doc_processor.process_file(file)
                if result.get("success"):
                    text = result.get("text_content", "") or result.get("content", "")
                    all_results[file.name] = {
                        "provider": "document_processor",
                        "success": True,
                        "text": text,
                        "confidence": 1.0,
                        "file_type": result["file_type"],
                        "content_type": result.get("content_type"),
                        "key_info": {},
                    }
                else:
                    all_results[file.name] = {
                        "provider": "document_processor",
                        "success": False,
                        "error": result.get("error", "处理失败"),
                        "file_type": suffix[1:],
                    }
            elif suffix in [".jpg", ".jpeg", ".png"]:
                result = self.ocr_service.recognize_image(file)
                all_results[file.name] = result

        # 对所有结果进行脱敏
        masked_results = {}
        for filename, result in all_results.items():
            if result.get("success") and result.get("text"):
                masked_text = self.privacy_masker.mask_text(result["text"])
                result["text"] = masked_text
                masked_results[filename] = result
            else:
                masked_results[filename] = result

        report = self.privacy_masker.get_masking_report()
        if report["total_masked"] > 0:
            LOGGER.info(
                f"脱敏处理: 共{report['total_masked']}处敏感信息",
                extra=_log_extra(stage="ocr"),
            )
        return masked_results

    # ── 出行日期提取 ────────────────────────────────────────────────────────

    def _extract_earliest_travel_date_from_ocr(self, ocr_results: Dict) -> Optional[datetime]:
        """从OCR文本中粗略提取最早出行日期(用于判断是否出境后才投保)。"""
        candidates: List[datetime] = []
        if not ocr_results:
            return None

        for filename, result in ocr_results.items():
            text = result.get("text") or ""
            if not text:
                continue

            keywords = [
                "出入境记录", "国家移民管理局", "出入境记录查询结果",
                "电子客票行程单", "行程单", "航班", "登机牌", "boarding pass", "Boarding Pass",
                "出入境", "入境", "出境",
            ]
            text_lower = text.lower()
            if not any(k.lower() in text_lower for k in keywords):
                continue

            travel_ctx_kw = ["起飞", "出发", "出境", "入境", "departure", "flight", "航班", "行程", "date"]
            exclude_ctx_kw = ["签证", "有效期", "签发", "visa", "visto", "valid", "until", "from",
                              "事故发生", "incident", "date of incident", "理赔", "索赔", "claim"]

            for m in re.finditer(r"20\d{2}[年\-/\.]\d{1,2}[月\-/\.]\d{1,2}", text):
                raw = m.group(0)
                cleaned = (
                    raw.replace("年", "-").replace("月", "-").replace("日", "")
                       .replace("/", "-").replace(".", "-")
                )
                left = max(0, m.start() - 30)
                right = min(len(text), m.end() + 30)
                ctx = text[left:right]
                ctx_lower = ctx.lower()

                has_travel_ctx = any(k.lower() in ctx_lower for k in travel_ctx_kw)
                if not has_travel_ctx:
                    continue
                has_exclude_ctx = any(k.lower() in ctx_lower for k in exclude_ctx_kw)
                if has_exclude_ctx and not (("出境" in ctx) or ("入境" in ctx)):
                    continue
                try:
                    d = datetime.strptime(cleaned, "%Y-%m-%d")
                    candidates.append(d)
                except Exception:
                    continue

        if not candidates:
            return None
        return min(candidates)