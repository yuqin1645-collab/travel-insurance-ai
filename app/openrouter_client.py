#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DashScope (Qwen) API客户端
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
from json_repair import repair_json

# json_repair 存在深度嵌套输入导致栈溢出的风险，设置输入长度上限
_JSON_REPAIR_MAX_LENGTH = 500_000

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
    json_repair 优先，手写修复作为补充。

    返回: (parsed_dict, should_retry)
    - parsed_dict 非 None 时解析成功
    - should_retry 表示是否应该继续重试（解析失败但还有重试机会）
    """
    # 1) json_repair 优先
    try:
        if len(content) > _JSON_REPAIR_MAX_LENGTH:
            raise ValueError(f"响应过长({len(content)} chars)，超过限制({_JSON_REPAIR_MAX_LENGTH})，跳过 JSON repair")
        return json.loads(repair_json(content)), False
    except Exception:
        pass

    # 2) 直接 json.loads
    try:
        return json.loads(content), False
    except json.JSONDecodeError as e:
        LOGGER.warning(f"[attempt={attempt}] JSON解析失败: {e}, 内容: {content[:500]}...")

    # 3) 正则提取 {.*}
    json_match = re.search(r'\{.*\}', content, re.DOTALL)
    if json_match:
        try:
            extracted = json_match.group()
            if len(extracted) > _JSON_REPAIR_MAX_LENGTH:
                raise ValueError(f"提取的 JSON 过长({len(extracted)} chars)，跳过 JSON repair")
            return json.loads(repair_json(extracted)), False
        except Exception:
            pass

    # 4) 手写转义修复
    fixed = _try_fix_json_string_escapes(content)
    if fixed is not None:
        LOGGER.info(f"[attempt={attempt}] JSON转义修复成功")
        return fixed, False

    # 5) 截断修复
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
    """DashScope (Qwen) API客户端"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.DASHSCOPE_API_KEY
        self.base_url = config.DASHSCOPE_BASE_URL

        if not self.api_key:
            raise ValueError("请设置 DASHSCOPE_API_KEY 环境变量")

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _model_supports_reasoning(self, model: str) -> bool:
        """判断模型是否支持 reasoning 参数"""
        reasoning_models = [
            'gemini-3', 'gemini-3.1', 'gemini-3-pro', 'gemini-3-flash',
            'gemini-3.1-pro', 'gemini-3.1-flash',
            'o1', 'o3', 'gpt-5',
            'claude-3.7', 'claude-3.8',
            'grok',
        ]
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
        """同步调用聊天完成API"""
        if model is None:
            model = config.get_model_by_difficulty(difficulty.value)

        temperature = temperature if temperature is not None else config.TEMPERATURE

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if self._model_supports_reasoning(model):
            payload["reasoning"] = {"effort": "low"}

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
        """异步调用聊天完成API（无内部重试，重试由调用方管理）"""
        if model is None:
            model = config.get_model_by_difficulty(difficulty.value)

        temperature = temperature if temperature is not None else config.TEMPERATURE

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if self._model_supports_reasoning(model):
            payload["reasoning"] = {"effort": "low"}

        if response_format:
            payload["response_format"] = response_format

        close_session = False
        if session is None:
            connector = aiohttp.TCPConnector()
            session = aiohttp.ClientSession(connector=connector, trust_env=True)
            close_session = True

        try:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=self.headers,
                json=payload,
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
