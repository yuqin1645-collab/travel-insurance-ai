#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vision API客户端
支持直接上传图片和PDF文件进行多模态审核
"""

import os
import sys
import re
import json
import asyncio
import base64
import logging

if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936', 'gb2312', 'gb18030'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() in ('gbk', 'cp936', 'gb2312', 'gb18030'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp
from json_repair import repair_json
from app.config import config

LOGGER = logging.getLogger(__name__)

_VISION_GLOBAL_CONCURRENCY = max(1, int(getattr(config, 'VISION_GLOBAL_CONCURRENCY', 6) or 6))
_VISION_SEMAPHORE = asyncio.Semaphore(_VISION_GLOBAL_CONCURRENCY)
_VISION_INFLIGHT = 0


class GeminiVisionClient:
    """视觉模型客户端 - 支持 OpenRouter (Gemini) 和 DashScope (Qwen VL)"""

    def __init__(self, api_key: Optional[str] = None, provider: str = 'dashscope'):
        """初始化客户端

        Args:
            api_key: API密钥
            provider: 'dashscope' (Qwen VL) 或 'openrouter' (Gemini)
        """
        # 优先使用 DashScope (Qwen)
        if provider == 'dashscope' or not api_key:
            self.api_key = api_key or config.DASHSCOPE_API_KEY or config.OPENROUTER_API_KEY
            self.base_url = config.DASHSCOPE_BASE_URL
            self.provider = 'dashscope'
        else:
            self.api_key = api_key or config.OPENROUTER_API_KEY
            self.base_url = config.OPENROUTER_BASE_URL
            self.provider = 'openrouter'

        if not self.api_key:
            raise ValueError("请设置 DASHSCOPE_API_KEY 或 OPENROUTER_API_KEY")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }
    
    def _encode_file_base64(self, file_path: Path) -> str:
        """将文件编码为base64"""
        with open(file_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    
    def _get_mime_type(self, file_path: Path) -> str:
        """获取文件的MIME类型"""
        suffix = file_path.suffix.lower()
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.webp': 'image/webp',
            '.pdf': 'application/pdf'
        }
        return mime_types.get(suffix, 'application/octet-stream')
    
    async def review_materials_with_vision(
        self,
        material_files: List[Path],
        prompt: str,
        session: Optional[aiohttp.ClientSession] = None
    ) -> Dict:
        """
        Review claim materials with Vision API.
        """
        prompt_clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", prompt)
        content_parts = [{"type": "text", "text": prompt_clean}]

        for file_path in material_files:
            if not file_path.exists():
                continue
            mime_type = self._get_mime_type(file_path)
            file_data = self._encode_file_base64(file_path)
            data_url = f"data:{mime_type};base64,{file_data}"
            if mime_type.startswith("image/"):
                content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
            elif mime_type == "application/pdf":
                content_parts.append({"type": "file", "file": {"url": data_url}})

        messages = [{"role": "user", "content": content_parts}]

        if self.provider == 'openrouter':
            vision_model = getattr(config, 'MODEL_VISION_OPENROUTER', 'google/gemini-2.5-pro-preview')
        else:
            vision_model = getattr(config, 'MODEL_VISION', 'qwen-vl-plus')
        payload = {
            "model": vision_model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 8100,
            "response_format": {"type": "json_object"},
        }

        close_session = False
        if session is None:
            connector = aiohttp.TCPConnector()
            session = aiohttp.ClientSession(
                connector=connector,
                trust_env=True
            )
            close_session = True

        try:
            proxy = None
            # DashScope/Qwen 请求直连，不走代理（国内服务）
            if self.provider != 'dashscope':
                proxy = (
                    os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
                    or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
                ) or None

            global _VISION_INFLIGHT
            async with _VISION_SEMAPHORE:
                _VISION_INFLIGHT += 1
                try:
                    LOGGER.debug(f"Vision API request start(provider={self.provider}, inflight={_VISION_INFLIGHT}/{_VISION_GLOBAL_CONCURRENCY})")
                    async with session.post(
                        f"{self.base_url}/chat/completions",
                        headers=self.headers,
                        json=payload,
                        proxy=proxy,
                        ssl=False if proxy else None,
                        timeout=aiohttp.ClientTimeout(total=300)
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            LOGGER.warning(f"Vision API error(provider={self.provider}): status={response.status}, response: {error_text[:500]}")
                        response.raise_for_status()
                        raw_bytes = await response.read()
                        result = self._robust_json_loads(raw_bytes)

                        content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
                        return self._parse_json_object_from_content(content)
                finally:
                    _VISION_INFLIGHT = max(0, _VISION_INFLIGHT - 1)

        except Exception as e:
            LOGGER.warning(f"Vision API call failed(provider={self.provider}): {e}")
            raise

        finally:
            if close_session:
                await session.close()

    def _robust_json_loads(self, raw_bytes: bytes) -> Dict:
        """
        多策略恢复含控制字符的 API 响应 JSON。
        策略1: 直接 utf-8 解码 + json.loads
        策略2: json_repair 修复
        策略3: 逐字符替换解码 + 剥离控制字符
        """
        last_err = None
        try:
            return json.loads(raw_bytes.decode('utf-8'))
        except json.JSONDecodeError as je:
            if "control character" not in str(je).lower():
                raise
            last_err = je

        # 策略2: json_repair 修复
        try:
            text = raw_bytes.decode('utf-8', errors='replace')
            return json.loads(repair_json(text))
        except Exception as e:
            last_err = e

        # 策略3: 全量替换解码 + 正则剥离控制字符
        try:
            cleaned = raw_bytes.decode('utf-8', errors='replace')
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", cleaned)
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_err = e

        raise last_err

    def _parse_json_object_from_content(self, content: str) -> Dict:
        """
        从模型返回内容中提取并解析 JSON object。
        优先使用 json_repair 库修复常见格式问题，手写状态机作为最后兜底。
        """

        if content is None:
            raise ValueError("vision response content is None")

        text = str(content).strip()
        # 去掉代码围栏（```json ... ```）
        text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
        text = text.replace("```", "").strip()

        def _is_empty_dict(obj: Any) -> bool:
            return isinstance(obj, dict) and len(obj) == 0

        # 1) json_repair 优先：覆盖控制字符、未转义换行、尾部逗号、缺引号等
        try:
            obj = json.loads(repair_json(text))
            if not _is_empty_dict(obj):
                return obj
        except Exception:
            pass

        # 2) 直接 json.loads（response_format=json_object 时应当可直接 parse）
        try:
            obj = json.loads(text)
            if not _is_empty_dict(obj):
                return obj
        except Exception:
            pass

        # 3) 从第一个到最后一个 '}' 之间截取
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                obj = json.loads(repair_json(candidate))
                if not _is_empty_dict(obj):
                    return obj
            except Exception:
                pass

        # 4) 括号深度 + 字符串状态机做平衡截取（最后兜底）
        start = text.find("{")
        if start == -1:
            raise ValueError("vision response: no '{' found to extract json object")

        depth = 0
        in_string = False
        escape = False
        end_idx = None
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == "{":
                depth += 1
                continue
            if ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

        if end_idx is None:
            raise ValueError("vision response: failed to balance json braces")

        candidate = text[start : end_idx + 1]
        try:
            obj = json.loads(repair_json(candidate))
        except Exception:
            obj = json.loads(candidate)
        return obj
