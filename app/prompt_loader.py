#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt加载和管理模块
"""

import re
from pathlib import Path
from typing import Dict, Optional, Tuple
from app.config import config


class PromptLoader:
    """Prompt加载器"""
    
    def __init__(self, prompts_dir: Optional[Path] = None):
        """初始化Prompt加载器"""
        self.prompts_dir = prompts_dir or config.PROMPTS_DIR
        # cache key: (namespace, prompt_name)
        self._cache: Dict[Tuple[str, str], str] = {}
    
    def load(self, prompt_name: str, namespace: str = "") -> str:
        """加载prompt模板"""
        ns = namespace or ""
        cache_key = (ns, prompt_name)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # 允许按 namespace 隔离 prompts：prompts/<namespace>/<prompt>.txt
        base_dir = self.prompts_dir / ns if ns else self.prompts_dir

        if not prompt_name.endswith(".txt"):
            prompt_file = base_dir / f"{prompt_name}.txt"
        else:
            prompt_file = base_dir / prompt_name

        # 兼容旧结构：若 namespace 下不存在，则回退到根 prompts 目录
        if ns and not prompt_file.exists():
            if not prompt_name.endswith(".txt"):
                prompt_file = self.prompts_dir / f"{prompt_name}.txt"
            else:
                prompt_file = self.prompts_dir / prompt_name
        
        if not prompt_file.exists():
            raise FileNotFoundError(f"Prompt文件不存在: {prompt_file}")
        
        with open(prompt_file, 'r', encoding='utf-8') as f:
            template = f.read()

        template = self._resolve_includes(template)
        self._cache[cache_key] = template
        return template

    def _resolve_includes(self, content: str) -> str:
        """将 {{include:block_name}} 替换为 _shared/ 目录下对应文件的内容"""
        def replacer(m: "re.Match") -> str:
            block_name = m.group(1).strip()
            shared_file = self.prompts_dir / "_shared" / f"{block_name}.txt"
            if shared_file.exists():
                return shared_file.read_text(encoding="utf-8")
            return m.group(0)  # 找不到时保留原文

        return re.sub(r'\{\{include:(.*?)\}\}', replacer, content)
    
    def format(self, prompt_name: str, namespace: str = "", **kwargs) -> str:
        """加载并格式化prompt，只替换已知 kwargs 占位符，其余保持原样"""
        template = self.load(prompt_name, namespace=namespace)
        # 先把 {{ }} 还原为字面量 { }，再只替换已知 kwargs
        # 避免 JSON 示例中的 {key: value} / 多行 { 触发 str.format() 的 KeyError
        for key, val in kwargs.items():
            template = template.replace("{" + key + "}", str(val))
        # 将剩余的 {{ }} 转换为字面 { }（Jinja 风格双括号转义）
        template = template.replace("{{", "{").replace("}}", "}")
        return template
    
    def list_prompts(self) -> list:
        """列出所有可用的prompt"""
        if not self.prompts_dir.exists():
            return []
        return [f.stem for f in self.prompts_dir.glob('*.txt')]
    
    def reload(self, prompt_name: str):
        """重新加载prompt(清除缓存)"""
        # 清理所有 namespace 下的同名 prompt
        for k in list(self._cache.keys()):
            if k[1] == prompt_name:
                del self._cache[k]
        return self.load(prompt_name)
    
    def clear_cache(self):
        """清除所有缓存"""
        self._cache.clear()


# 创建全局prompt加载器实例
prompt_loader = PromptLoader()
