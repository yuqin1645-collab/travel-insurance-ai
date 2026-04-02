#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR缓存管理模块
基于文件哈希的持久化缓存
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timedelta
from app.config import config
from app.logging_utils import LOGGER, log_extra as _log_extra


class OCRCache:
    """OCR结果缓存管理器"""
    
    def __init__(
        self,
        cache_dir: Path = None,
        enabled: bool = None,
        expire_days: int = None,
        namespace: str | None = None,
    ):
        """
        初始化缓存管理器
        
        Args:
            cache_dir: 缓存目录
            enabled: 是否启用缓存
            expire_days: 缓存过期天数
            namespace: 命名空间（建议使用 claim_type），用于隔离不同案件类型的缓存
        """
        base_dir = cache_dir or config.OCR_CACHE_DIR
        ns = (namespace or "").strip()
        self.cache_dir = (base_dir / ns) if ns else base_dir
        self.enabled = enabled if enabled is not None else config.OCR_CACHE_ENABLED
        self.expire_days = expire_days if expire_days is not None else config.OCR_CACHE_EXPIRE_DAYS
        
        # 创建缓存目录
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.index_file = self.cache_dir / 'index.json'
            self._load_index()
    
    def _load_index(self):
        """加载缓存索引"""
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    self.index = json.load(f)
            except Exception as e:
                LOGGER.warning(f"加载缓存索引失败: {e}", extra=_log_extra(stage="ocr_cache"))
                self.index = {}
        else:
            self.index = {}
    
    def _save_index(self):
        """保存缓存索引"""
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(self.index, f, ensure_ascii=False, indent=2)
        except Exception as e:
            LOGGER.warning(f"保存缓存索引失败: {e}", extra=_log_extra(stage="ocr_cache"))
    
    def _calculate_file_hash(self, file_path: Path) -> str:
        """计算文件MD5哈希"""
        md5_hash = hashlib.md5()
        with open(file_path, 'rb') as f:
            # 分块读取,避免大文件占用过多内存
            for chunk in iter(lambda: f.read(8192), b''):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    
    def _get_cache_path(self, file_hash: str) -> Path:
        """获取缓存文件路径"""
        # 使用哈希的前两位作为子目录,避免单个目录文件过多
        subdir = file_hash[:2]
        cache_subdir = self.cache_dir / subdir
        cache_subdir.mkdir(exist_ok=True)
        return cache_subdir / f"{file_hash}.json"
    
    def _is_expired(self, cache_time: str) -> bool:
        """检查缓存是否过期"""
        try:
            cache_dt = datetime.fromisoformat(cache_time)
            expire_dt = cache_dt + timedelta(days=self.expire_days)
            return datetime.now() > expire_dt
        except Exception:
            return True
    
    def get(self, file_path: Path) -> Optional[Dict]:
        """
        获取缓存的OCR结果
        
        Args:
            file_path: 图片文件路径
            
        Returns:
            缓存的OCR结果,如果不存在或已过期则返回None
        """
        if not self.enabled:
            return None
        
        try:
            file_hash = self._calculate_file_hash(file_path)
            cache_path = self._get_cache_path(file_hash)
            
            if not cache_path.exists():
                return None
            
            # 读取缓存
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
            
            # 检查是否过期
            if self._is_expired(cache_data.get('cache_time', '')):
                LOGGER.info(f"缓存已过期: {file_path.name}", extra=_log_extra(stage="ocr_cache"))
                cache_path.unlink()  # 删除过期缓存
                if file_hash in self.index:
                    del self.index[file_hash]
                    self._save_index()
                return None
            
            LOGGER.info(f"使用缓存: {file_path.name}", extra=_log_extra(stage="ocr_cache"))
            return cache_data.get('result')
            
        except Exception as e:
            LOGGER.warning(f"读取缓存失败: {e}", extra=_log_extra(stage="ocr_cache"))
            return None
    
    def set(self, file_path: Path, result: Dict):
        """
        保存OCR结果到缓存
        
        Args:
            file_path: 图片文件路径
            result: OCR识别结果
        """
        if not self.enabled:
            return
        
        try:
            file_hash = self._calculate_file_hash(file_path)
            cache_path = self._get_cache_path(file_hash)
            
            # 构建缓存数据
            cache_data = {
                'file_name': file_path.name,
                'file_hash': file_hash,
                'cache_time': datetime.now().isoformat(),
                'result': result
            }
            
            # 保存缓存
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
            # 更新索引
            self.index[file_hash] = {
                'file_name': file_path.name,
                'cache_path': str(cache_path),
                'cache_time': cache_data['cache_time']
            }
            self._save_index()
            
        except Exception as e:
            LOGGER.warning(f"保存缓存失败: {e}", extra=_log_extra(stage="ocr_cache"))
    
    def clear(self):
        """清除所有缓存"""
        if not self.enabled:
            return
        
        try:
            # 删除所有缓存文件
            for cache_file in self.cache_dir.rglob('*.json'):
                if cache_file != self.index_file:
                    cache_file.unlink()
            
            # 清空索引
            self.index = {}
            self._save_index()
            
            LOGGER.info("缓存已清除", extra=_log_extra(stage="ocr_cache"))
        except Exception as e:
            LOGGER.warning(f"清除缓存失败: {e}", extra=_log_extra(stage="ocr_cache"))
    
    def clean_expired(self):
        """清理过期的缓存"""
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
                LOGGER.info(f"清理了 {expired_count} 个过期缓存", extra=_log_extra(stage="ocr_cache"))
        except Exception as e:
            LOGGER.warning(f"清理过期缓存失败: {e}", extra=_log_extra(stage="ocr_cache"))
    
    def get_stats(self) -> Dict:
        """获取缓存统计信息"""
        if not self.enabled:
            return {
                'enabled': False,
                'total': 0,
                'size': 0
            }
        
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


def test_ocr_cache():
    """测试OCR缓存"""
    LOGGER.info("测试OCR缓存...", extra=_log_extra(stage="ocr_cache"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="ocr_cache"))
    
    cache = OCRCache()
    
    # 显示统计信息
    stats = cache.get_stats()
    LOGGER.info(f"缓存状态: {'启用' if stats['enabled'] else '禁用'}", extra=_log_extra(stage="ocr_cache"))
    LOGGER.info(f"缓存数量: {stats.get('total', 0)}", extra=_log_extra(stage="ocr_cache"))
    LOGGER.info(f"缓存大小: {stats.get('size_mb', 0)} MB", extra=_log_extra(stage="ocr_cache"))
    LOGGER.info(f"缓存目录: {stats.get('cache_dir', 'N/A')}", extra=_log_extra(stage="ocr_cache"))
    LOGGER.info(f"过期天数: {stats.get('expire_days', 0)}", extra=_log_extra(stage="ocr_cache"))
    
    LOGGER.info("=" * 60, extra=_log_extra(stage="ocr_cache"))


if __name__ == "__main__":
    test_ocr_cache()
