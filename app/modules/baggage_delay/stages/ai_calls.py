"""
baggage_delay AI 调用层 — 从 AIClaimReviewer 提取的独立函数。
每个函数只负责构建 prompt 并调用模型，不依赖类状态。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import aiohttp

from app.openrouter_client import OpenRouterClient, TaskDifficulty
from app.prompt_loader import PromptLoader


async def run_baggage_delay_parse(
    client: OpenRouterClient,
    prompt_loader: PromptLoader,
    namespace: str,
    claim_info: Dict[str, Any],
    free_text: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    prompt = prompt_loader.format(
        "01_data_parse_and_timezone",
        namespace=namespace,
        claim_info_json=json.dumps(claim_info, ensure_ascii=False),
        free_text=free_text or "",
    )
    return await client.chat_completion_json_async(
        messages=[{"role": "user", "content": prompt}],
        difficulty=TaskDifficulty.MEDIUM,
        session=session,
    )


async def run_baggage_delay_audit(
    client: OpenRouterClient,
    prompt_loader: PromptLoader,
    namespace: str,
    claim_info: Dict[str, Any],
    parsed: Dict[str, Any],
    policy_terms_excerpt: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    prompt = prompt_loader.format(
        "02_audit_decision",
        namespace=namespace,
        parsed_json=json.dumps(parsed, ensure_ascii=False),
        policy_terms_excerpt=policy_terms_excerpt or "",
        claim_info_json=json.dumps(claim_info, ensure_ascii=False),
    )
    return await client.chat_completion_json_async(
        messages=[{"role": "user", "content": prompt}],
        difficulty=TaskDifficulty.MEDIUM,
        session=session,
    )


async def run_pir_receipt_time_extract(
    vision_client: Any,
    prompt_loader: PromptLoader,
    namespace: str,
    attachment_paths: List[Path],
    claim_info: Dict[str, Any],
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    if not attachment_paths:
        return {}
    prompt = prompt_loader.format(
        "00b_pir_receipt_time_extract",
        namespace=namespace,
        claim_info_json=json.dumps(claim_info, ensure_ascii=False, indent=2),
    )
    return await vision_client.review_materials_with_vision(
        material_files=attachment_paths,
        prompt=prompt,
        session=session,
    )