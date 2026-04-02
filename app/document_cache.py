#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档处理缓存模块
基于文件哈希的持久化缓存，支持PDF、Word、图片等
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timedelta
from app.config import config


class DocumentCache:
    """文档处理结果缓存管理器"""
    
    def __init__(self, cache_dir: Path = None, enabled: bool = None, expire_days: int = None):
        self.cache_dir = cache_dir or config.DOC_CACHE_DIR
        self.enabled = enabled if enabled is not None else config.DOC_CACHE_ENABLED
        self.expire_days = expire_days if expire_days is not None else config.DOC_CACHE_EXPIRE_DAYS
        
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.index_file = self.cache_dir / 'index.json'
            self._load_index()
    
    def _load_index(self):
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    self.index = json.load(f)
            except Exception as e:
                print(f"警告: 加载文档缓存索引失败: {e}")
                self.index = {}
        else:
            self.index = {}
    
    def _save_index(self):
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"警告: 保存文档缓存索引失败: {e}")
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    
    def _get_cache_path(self, file_hash: str) -> Path:
        subdir = file_hash[:2]
        cache_subdir = self.cache_dir / subdir
        cache_subdir.mkdir(exist_ok=True)
        return cache_subdir / f"{file_hash}.json"
    
    def _is_expired(self, cache_time: str) -> bool:
        try:
            cache_dt = datetime.fromisoformat(cache_time)
            expire_dt = cache_dt + timedelta(days=self.expire_days)
            return datetime.now() > expire_dt
        except Exception:
            return True
    
    def get(self, file_path: Path) -> Optional[Dict]:
        if not self.enabled:
            return None
        
        try:
            file_hash = self._calculate_file_hash(file_path)
            cache_path = self._get_cache_path(file_hash)
            
            if not cache_path.exists():
                return None
            
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            if self._is_expired(cache_data.get('cache_time', '')):
                print(f"  文档缓存已过期: {file_path.name}")
                cache_path.unlink()
                if file_hash in self.index:
                    del self.index[file_hash]
                    self._save_index()
                return None
            
            print(f"  使用文档缓存: {file_path.name}")
            return cache_data.get('result')
            
        except Exception as e:
            print(f"  读取文档缓存失败: {e}")
            return None
    
    def set(self, file_path: Path, result: Dict):
        if not self.enabled:
            return
        
        try:
            file_hash = self._calculate_file_hash(file_path)
            cache_path = self._get_cache_path(file_hash)
            
            cache_data = {
                'file_name': file_path.name,
                'file_hash': file_hash,
                'cache_time': datetime.now().isoformat(),
                'result': result
            }
            
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            self.index[file_hash] = {
                'file_name': file_path.name,
                'cache_path': str(cache_path),
                'cache_time': cache_data['cache_time']
            }
            self._save_index()
            
        except Exception as e:
            print(f"  保存文档缓存失败: {e}")
    
    def clear(self):
        if not self.enabled:
            return
        
        try:
            for cache_file in self.cache_dir.rglob('*.json'):
                if cache_file != self.index_file:
                    cache_file.unlink()
            self.index = {}
            self._save_index()
            print("文档缓存已清除")
        except Exception as e:
            print(f"清除文档缓存失败: {e}")
    
    def clean_expired(self):
        if not self.enabled:
            return
        
        expired_count = 0
        try:
            for file_hash, info in list(self.index.items()):
                if self._is_expired(info.get('cache_time', '')):
                    cache_path = Path(info['cache_path'])
                    if cache_path.exists():
                        cache_path.unlink()
                    del self.index[file_hash]
                    expired_count += 1
            
            if expired_count > 0:
                self._save_index()
                print(f"清理了 {expired_count} 个过期文档缓存")
        except Exception as e:
            print(f"清理过期文档缓存失败: {e}")
    
    def get_stats(self) -> Dict:
        if not self.enabled:
            return {'enabled': False, 'total': 0, 'size': 0}
        
        total_size = 0
        for cache_file in self.cache_dir.rglob('*.json'):
            total_size += cache_file.stat().st_size
        
        return {
            'enabled': True,
            'total': len(self.index),
            'size': total_size,
            'size_mb': round(total_size / 1024 / 1024, 2),
            'cache_dir': str(self.cache_dir),
            'expire_days': self.expire_days
        }


document_cache = DocumentCache()