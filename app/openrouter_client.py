#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenRouter API客户端
根据任务难度自动选择合适的模型
支持同步和异步调用
"""

import os
import re
import json
import sys
import logging

# Windows GBK stdout 无法打印 emoji，统一设置为 utf-8
if sys.stdout.encoding and sys.stdout.encoding.lower() in ('gbk', 'cp936', 'gb2312', 'gb18030'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.lower() in ('gbk', 'cp936', 'gb2312', 'gb18030'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import requests
import asyncio
import time
import aiohttp
from typing import Dict, List, Optional
from enum import Enum
from app.config import config

LOGGER = logging.getLogger(__name__)


def _try_repair_truncated_json(content: str) -> Optional[Dict]:
    """尝试修复被截断的 JSON（末尾缺少若干 `}`）。
    最多补 5 个 `}`，逐一尝试解析，成功则返回结果，否则返回 None。
    """
    s = content.strip()
    for i in range(1, 6):
        try:
            return json.loads(s + "}" * i)
        except json.JSONDecodeError:
            continue
    return None


def _try_fix_json_string_escapes(content: str) -> Optional[Dict]:
    """尝试修复 JSON 中的字符串未转义问题（换行符、引号等）。

    常见问题：
    - explanation 字段包含未转义的换行符（实际换行而非 \\n）
    - 字符串内包含未转义的双引号

    修复策略：
    1. 找到所有 "key": "value" 模式的字符串值
    2. 对值内部的换行符替换为 \\n
    3. 对值内部的双引号替换为 \\"
    """

    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 修复策略：逐字段处理字符串值
    def fix_string_value(match):
        key = match.group(1)
        value = match.group(2)
        # 修复值内部的换行符和引号
        fixed_value = value.replace('\n', '\\n').replace('\r', '\\r')
        # 修复值内部未转义的双引号（但不处理已经转义的）
        # 简单策略：值内部的 " 替换为 \"，但要排除边界引号
        # 更安全的做法：逐字符扫描
        chars = []
        for i, c in enumerate(fixed_value):
            if c == '"' and i > 0 and i < len(fixed_value) - 1:
                # 检查前面是否有转义符
                if chars and chars[-1] != '\\':
                    chars.append('\\')
            chars.append(c)
        fixed_value = ''.join(chars)
        return f'"{key}": "{fixed_value}"'

    # 匹配 "key": "value" 模式（value 可能含未转义内容）
    # 使用非贪婪匹配，但允许多行
    pattern = r'"([^"]+)":\s*"([^"]*(?:[^"\\]|\\.)*?)"'
    fixed_content = re.sub(pattern, fix_string_value, content, flags=re.DOTALL)

    try:
        return json.loads(fixed_content)
    except json.JSONDecodeError:
        return None


def _parse_json_with_fallbacks(
    content: str,
    attempt: int,
    max_retries: int,
    sleep_fn,
) -> Dict:
    """
    统一 JSON 解析 + 修复逻辑（同步/异步共享）。

    返回: (parsed_dict, should_retry)
    - parsed_dict 非 None 时解析成功
    - should_retry 表示是否应该继续重试（解析失败但还有重试机会）
    """
    try:
        return json.loads(content), False
    except json.JSONDecodeError as e:
        LOGGER.warning(f"[attempt={attempt}] JSON解析失败: {e}, 内容: {content[:500]}...")

    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group()), False
        except json.JSONDecodeError:
            pass

    fixed = _try_fix_json_string_escapes(content)
    if fixed is not None:
        LOGGER.info(f"[attempt={attempt}] JSON转义修复成功")
        return fixed, False

    repaired = _try_repair_truncated_json(content)
    if repaired is not None:
        LOGGER.info(f"[attempt={attempt}] JSON截断修复成功")
        return repaired, False

    should_retry = attempt < max_retries
    if should_retry:
        sleep_fn(1 * attempt)
    return None, should_retry


class TaskDifficulty(Enum):
    """任务难度等级"""
    SIMPLE = "simple"      # 简单任务: 信息提取、格式转换
    MEDIUM = "medium"      # 中等任务: 材料审核、规则匹配
    HARD = "hard"          # 困难任务: 复杂判断、多步推理
    EXPERT = "expert"      # 专家任务: 法律条款解释、边界案例


class OpenRouterClient:
    """API客户端 - 支持 OpenRouter 和 DashScope (Qwen)"""

    def __init__(self, api_key: Optional[str] = None, provider: str = 'dashscope'):
        """初始化客户端

        Args:
            api_key: API密钥，默认使用配置文件中的密钥
            provider: 'dashscope' (Qwen) 或 'openrouter'
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
            raise ValueError("请设置 DASHSCOPE_API_KEY 或 OPENROUTER_API_KEY 环境变量")

        # DashScope 和 OpenRouter 兼容格式
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

        # OpenRouter 特有 headers
        if self.provider == 'openrouter':
            self.headers["HTTP-Referer"] = os.getenv("OPENROUTER_REFERRER", "http://localhost:8000")
            self.headers["X-Title"] = os.getenv("OPENROUTER_TITLE", "Claim Review System")
    
    def _model_supports_reasoning(self, model: str) -> bool:
        """判断模型是否支持 reasoning 参数"""
        # Gemini 3.x 系列支持 reasoning
        reasoning_models = [
            'gemini-3',
            'gemini-3.1',
            'gemini-3-pro',
            'gemini-3-flash',
            'gemini-3.1-pro',
            'gemini-3.1-flash',
            # OpenAI o 系列和 GPT-5 系列
            'o1',
            'o3',
            'gpt-5',
            # Anthropic Claude 3.7+
            'claude-3.7',
            'claude-3.8',
            # Grok
            'grok',
        ]
        
        # 检查模型名称是否包含这些关键词
        model_lower = model.lower()
        return any(keyword in model_lower for keyword in reasoning_models)
    
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        difficulty: TaskDifficulty = TaskDifficulty.MEDIUM,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict] = None
    ) -> Dict:
        """同步调用OpenRouter聊天完成API"""
        if model is None:
            model = config.get_model_by_difficulty(difficulty.value)
        
        temperature = temperature if temperature is not None else config.TEMPERATURE

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        # 为支持 reasoning 的模型添加 reasoning 参数
        if self._model_supports_reasoning(model):
            payload["reasoning"] = {
                "effort": "low"  # 使用低推理强度以节省时间和成本
            }

        if response_format:
            payload["response_format"] = response_format

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=config.TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.RequestException as e:
            LOGGER.error(f"API调用失败: {e}, 模型: {model}")
            LOGGER.debug(f"Payload: {json.dumps(payload, ensure_ascii=False, indent=2)}")
            if hasattr(e, 'response') and e.response is not None:
                LOGGER.error(f"状态码: {e.response.status_code}, 响应: {e.response.text}")
            raise
    
    async def chat_completion_async(
        self,
        messages: List[Dict[str, str]],
        difficulty: TaskDifficulty = TaskDifficulty.MEDIUM,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict] = None,
        session: Optional[aiohttp.ClientSession] = None
    ) -> Dict:
        """异步调用OpenRouter聊天完成API（无内部重试，重试由调用方管理）"""
        if model is None:
            model = config.get_model_by_difficulty(difficulty.value)

        temperature = temperature if temperature is not None else config.TEMPERATURE

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        # 为支持 reasoning 的模型添加 reasoning 参数
        if self._model_supports_reasoning(model):
            payload["reasoning"] = {
                "effort": "low"
            }

        if response_format:
            payload["response_format"] = response_format

        # 如果没有提供session,创建临时session（使用系统代理）
        close_session = False
        if session is None:
            connector = aiohttp.TCPConnector()
            session = aiohttp.ClientSession(connector=connector, trust_env=True)
            close_session = True

        # 代理：仅 OpenRouter 国外服务使用代理，DashScope/Qwen 直连
        proxy = None
        if self.provider != 'dashscope':
            proxy = (
                os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
                or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
            ) or None

        try:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=config.TIMEOUT)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    LOGGER.warning(f"异步调用错误: 状态码={response.status}, 响应: {error_text[:500]}")
                response.raise_for_status()
                return await response.json()
        finally:
            if close_session:
                await session.close()
    
    def extract_content(self, response: Dict, stage_name: str = "unknown") -> str:
        """从API响应中提取内容"""
        try:
            # 安全提取 choices[0].message.content
            choices = response.get('choices', [])
            if not choices:
                LOGGER.warning(f"[{stage_name}] 响应中没有 choices 字段, 结构: {list(response.keys())}")
                raise KeyError("choices 为空")

            first_choice = choices[0]
            message = first_choice.get('message', {})
            if not message:
                LOGGER.warning(f"[{stage_name}] choices[0] 中没有 message 字段, 内容: {first_choice}")
                raise KeyError("message 为空")

            content = message.get('content', '')
            if not content:
                LOGGER.warning(f"[{stage_name}] message.content 为空, message: {message}")

            # 记录原始 LLM 响应内容
            LOGGER.debug(f"[{stage_name}] LLM原始响应内容 ({len(content)} 字符):\n{content[:2000] if len(content) > 2000 else content}")

            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json_match.group()
            return content
        except (KeyError, IndexError) as e:
            LOGGER.error(f"[{stage_name}] 解析响应失败: {e}, 响应: {json.dumps(response, indent=2, ensure_ascii=False)[:1000]}")
            raise
    
    def chat_completion_json(
        self,
        messages: List[Dict[str, str]],
        difficulty: TaskDifficulty = TaskDifficulty.MEDIUM,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """同步调用API并返回JSON格式结果（带重试机制）"""
        if messages and messages[-1]['role'] == 'user':
            original_content = messages[-1]['content']
            if 'JSON' not in original_content and 'json' not in original_content:
                messages[-1]['content'] = original_content + "\n\n请以JSON格式返回结果。"

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self.chat_completion(
                    messages=messages,
                    difficulty=difficulty,
                    response_format={"type": "json_object"},
                    **kwargs
                )

                content = self.extract_content(response, stage_name=f"chat_completion_json[attempt={attempt}]")

                result, should_retry = _parse_json_with_fallbacks(content, attempt, max_retries, time.sleep)
                if result is not None:
                    return result
                if should_retry:
                    continue
                raise json.JSONDecodeError("全部修复尝试失败", content, 0)

            except Exception as e:
                last_error = e
                LOGGER.warning(f"[attempt={attempt}] API调用异常: {e}")
                if attempt < max_retries:
                    time.sleep(1 * attempt)
                    continue

        raise last_error or RuntimeError("chat_completion_json 全部重试失败")

    async def chat_completion_json_async(
        self,
        messages: List[Dict[str, str]],
        difficulty: TaskDifficulty = TaskDifficulty.MEDIUM,
        session: Optional[aiohttp.ClientSession] = None,
        max_retries: int = 3,
        **kwargs
    ) -> Dict:
        """异步调用API并返回JSON格式结果（带重试机制）"""
        if messages and messages[-1]['role'] == 'user':
            original_content = messages[-1]['content']
            if 'JSON' not in original_content and 'json' not in original_content:
                messages[-1]['content'] = original_content + "\n\n请以JSON格式返回结果。"

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                response = await self.chat_completion_async(
                    messages=messages,
                    difficulty=difficulty,
                    response_format={"type": "json_object"},
                    session=session,
                    **kwargs
                )

                content = self.extract_content(response, stage_name=f"chat_completion_json_async[attempt={attempt}]")

                result, should_retry = _parse_json_with_fallbacks(content, attempt, max_retries, asyncio.sleep)
                if result is not None:
                    return result
                if should_retry:
                    continue
                raise json.JSONDecodeError("全部修复尝试失败", content, 0)

            except Exception as e:
                last_error = e
                LOGGER.warning(f"[attempt={attempt}] API调用异常: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1 * attempt)
                    continue

        raise last_error or RuntimeError("chat_completion_json_async 全部重试失败")
