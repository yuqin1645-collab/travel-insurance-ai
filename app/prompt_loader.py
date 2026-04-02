#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt加载和管理模块
"""

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
        
        self._cache[cache_key] = template
        return template
    
    def format(self, prompt_name: str, namespace: str = "", **kwargs) -> str:
        """加载并格式化prompt"""
        template = self.load(prompt_name, namespace=namespace)
        return template.format(**kwargs)
    
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
