#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gemini Vision API客户端
支持直接上传图片和PDF文件进行多模态审核
"""

import os
import base64
from pathlib import Path
from typing import Dict, List, Optional
import aiohttp
from app.config import config


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
        使用Gemini Vision审核材料
        
        Args:
            material_files: 材料文件列表(图片、PDF等)
            prompt: 审核提示词
            session: aiohttp会话
        
        Returns:
            审核结果
        """
        # 构建消息内容
        content_parts = [{"type": "text", "text": prompt}]
        
        # 添加所有材料文件
        for file_path in material_files:
            if not file_path.exists():
                continue
            
            # 编码文件
            mime_type = self._get_mime_type(file_path)
            file_data = self._encode_file_base64(file_path)
            
            data_url = f"data:{mime_type};base64,{file_data}"

            # OpenRouter: 图片使用 image_url；PDF使用 file（Universal PDF Support）
            if mime_type.startswith("image/"):
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_url}
                })
            elif mime_type == "application/pdf":
                content_parts.append({
                    "type": "file",
                    "file": {"url": data_url}
                })
            else:
                # 其他类型暂不发送
                continue
        
        messages = [
            {
                "role": "user",
                "content": content_parts
            }
        ]

        # 根据 provider 选择对应的视觉模型
        if self.provider == 'openrouter':
            vision_model = getattr(config, 'MODEL_VISION_OPENROUTER', 'google/gemini-2.5-pro-preview')
        else:
            vision_model = getattr(config, 'MODEL_VISION', 'qwen-vl-plus')
        payload = {
            "model": vision_model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 8000,
        }

        # OpenRouter 支持 response_format，DashScope 可能不支持
        if self.provider == 'openrouter':
            payload["response_format"] = {"type": "json_object"}
        
        # 如果没有提供session,创建临时session
        close_session = False
        if session is None:
            connector = aiohttp.TCPConnector()
            session = aiohttp.ClientSession(
                connector=connector,
                trust_env=True
            )
            close_session = True
        
        try:
            # 代理：优先读环境变量，读不到则依赖 trust_env=True 自动使用系统代理
            proxy = (
                os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
                or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
            ) or None

            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                proxy=proxy if proxy else None,
                ssl=False if proxy else None,  # 仅在明确指定代理时跳过SSL验证
                timeout=aiohttp.ClientTimeout(total=300)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    print(f"Gemini Vision API错误:")
                    print(f"  状态码: {response.status}")
                    print(f"  响应: {error_text[:500]}")
                response.raise_for_status()
                raw_bytes = await response.read()
                import json as _json
                result = _json.loads(raw_bytes.decode('utf-8'))
                
                # 提取内容
                content = result['choices'][0]['message']['content']

                # 解析 JSON（兼容模型偶发输出前后带少量文本/代码围栏）
                return self._parse_json_object_from_content(content)
        
        except Exception as e:
            print(f"Gemini Vision API调用失败: {e}")
            raise
        
        finally:
            if close_session:
                await session.close()

    def _parse_json_object_from_content(self, content: str) -> Dict:
        """
        尽量稳健地从模型返回内容中提取并解析 JSON object。
        目标：避免因贪婪截取导致的 json.loads 语法错误（如 Expecting ',' delimiter）。
        """
        import json
        import re

        if content is None:
            raise ValueError("vision response content is None")

        text = str(content).strip()
        # 去掉代码围栏（```json ... ```）
        text = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE)
        text = text.replace("```", "").strip()

        def _is_empty_dict(obj: Any) -> bool:
            return isinstance(obj, dict) and len(obj) == 0

        # 1) 先直接尝试（response_format=json_object 理论上应当可直接 parse）
        try:
            obj = json.loads(text)
            if not _is_empty_dict(obj):
                return obj
        except Exception:
            pass

        # 2) 再尝试：从第一个到最后一个 '}' 之间截取
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                obj = json.loads(candidate)
                if not _is_empty_dict(obj):
                    return obj
            except Exception:
                pass

        # 3) 兜底：从第一个 '{' 开始，尝试多个候选结束位置（尽量找到可解析的 JSON object）
        if start != -1:
            # 只尝试最后若干个 '}'，避免 O(n^2) 太慢
            ends = [m.start() for m in re.finditer(r"}", text)]
            for end_i in sorted(ends, reverse=True)[:25]:
                candidate = text[start : end_i + 1]
                try:
                    obj = json.loads(candidate)
                    if not _is_empty_dict(obj):
                        return obj
                except Exception:
                    continue

        # 4) 最后兜底：用“括号深度 + 字符串状态”做平衡截取（避免前后文本干扰）
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
        obj = json.loads(candidate)
        return obj
