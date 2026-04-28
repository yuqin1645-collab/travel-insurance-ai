#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档处理模块
支持PDF、DOCX、图片等多种格式
可以转换为文本或直接发送给AI
"""

import base64
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
import json
from app.document_cache import document_cache

LOGGER = logging.getLogger(__name__)


class DocumentProcessor:
    """文档处理器 - 支持多种格式"""
    
    def __init__(self):
        self.supported_image_formats = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        self.supported_doc_formats = ['.pdf', '.docx', '.doc', '.txt']
    
    def process_file(self, file_path: Path) -> Dict:
        """
        处理文件,返回统一格式
        
        Returns:
            {
                'file_name': '文件名',
                'file_type': 'pdf/image/docx/txt',
                'content_type': 'base64/text',
                'content': '内容',
                'success': True/False,
                'error': '错误信息'
            }
        """
        # 先检查缓存
        cached_result = document_cache.get(file_path)
        if cached_result:
            LOGGER.debug(f"使用缓存: {file_path.name}")
            return cached_result
        
        suffix = file_path.suffix.lower()
        result = None
        
        try:
            if suffix in self.supported_image_formats:
                result = self._process_image(file_path)
            elif suffix == '.pdf':
                result = self._process_pdf(file_path)
            elif suffix in ['.docx', '.doc']:
                result = self._process_docx(file_path)
            elif suffix == '.txt':
                result = self._process_txt(file_path)
            else:
                result = {
                    'file_name': file_path.name,
                    'file_type': 'unsupported',
                    'success': False,
                    'error': f'不支持的文件格式: {suffix}'
                }
        except Exception as e:
            result = {
                'file_name': file_path.name,
                'file_type': suffix[1:],
                'success': False,
                'error': str(e)
            }
        
        # 保存缓存
        if result:
            document_cache.set(file_path, result)
        
        return result
    
    def _process_image(self, file_path: Path) -> Dict:
        """处理图片文件 - 转为base64"""
        with open(file_path, 'rb') as f:
            image_data = f.read()
            base64_data = base64.b64encode(image_data).decode('utf-8')
        
        return {
            'file_name': file_path.name,
            'file_type': 'image',
            'content_type': 'base64',
            'content': base64_data,
            'mime_type': self._get_mime_type(file_path.suffix),
            'success': True
        }
    
    def _process_pdf(self, file_path: Path) -> Dict:
        """处理PDF文件"""
        # 方案1: 转为base64直接发送给AI (推荐)
        with open(file_path, 'rb') as f:
            pdf_data = f.read()
            base64_data = base64.b64encode(pdf_data).decode('utf-8')
        
        # 方案2: 提取文本(备用)
        text_content = self._extract_pdf_text(file_path)
        
        return {
            'file_name': file_path.name,
            'file_type': 'pdf',
            'content_type': 'base64',
            'content': base64_data,
            'text_content': text_content,  # 备用文本
            'mime_type': 'application/pdf',
            'success': True
        }
    
    def _process_docx(self, file_path: Path) -> Dict:
        """处理DOCX文件 - 提取文本"""
        try:
            from docx import Document
            doc = Document(file_path)
            text = '\n'.join([para.text for para in doc.paragraphs])
            
            return {
                'file_name': file_path.name,
                'file_type': 'docx',
                'content_type': 'text',
                'content': text,
                'success': True
            }
        except ImportError:
            # 如果没有安装python-docx,返回错误
            return {
                'file_name': file_path.name,
                'file_type': 'docx',
                'success': False,
                'error': '需要安装python-docx: pip install python-docx'
            }
    
    def _process_txt(self, file_path: Path) -> Dict:
        """处理TXT文件"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        
        return {
            'file_name': file_path.name,
            'file_type': 'txt',
            'content_type': 'text',
            'content': text,
            'success': True
        }
    
    def _extract_pdf_text(self, file_path: Path) -> str:
        """从PDF提取文本 - 使用pyMuPDF"""
        try:
            import fitz  # pyMuPDF
            doc = fitz.open(file_path)
            text = ''
            for page in doc:
                text += page.get_text() + '\n'
            doc.close()
            return text.strip()
        except Exception as e:
            return f"PDF文本提取失败: {e}"
    
    def _get_mime_type(self, suffix: str) -> str:
        """获取MIME类型"""
        mime_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.pdf': 'application/pdf',
        }
        return mime_types.get(suffix.lower(), 'application/octet-stream')
    
    def prepare_for_ai(self, file_path: Path) -> Dict:
        """
        准备文件供AI处理
        返回适合发送给Claude等AI的格式
        """
        result = self.process_file(file_path)
        
        if not result['success']:
            return result
        
        # 构建AI可以理解的格式
        if result['content_type'] == 'base64':
            # 图片或PDF - 使用vision API
            return {
                'type': 'image' if result['file_type'] == 'image' else 'document',
                'source': {
                    'type': 'base64',
                    'media_type': result.get('mime_type', 'application/octet-stream'),
                    'data': result['content']
                },
                'file_name': result['file_name'],
                'success': True
            }
        else:
            # 文本内容
            return {
                'type': 'text',
                'content': result['content'],
                'file_name': result['file_name'],
                'success': True
            }
    
    def batch_process(self, file_paths: List[Path], show_progress: bool = True) -> Dict[str, Dict]:
        """批量处理文件"""
        results = {}
        total = len(file_paths)
        
        for i, file_path in enumerate(file_paths, 1):
            if show_progress:
                LOGGER.debug(f"处理文件: {i}/{total} - {file_path.name}")
            
            results[file_path.name] = self.process_file(file_path)
        
        return results


def test_document_processor():
    """测试文档处理器"""
    print("测试文档处理器...")
    print("=" * 60)
    
    processor = DocumentProcessor()
    
    # 查找测试文件
    from pathlib import Path
    claims_dir = Path('claims_data')
    
    test_files = []
    for info_file in claims_dir.rglob("claim_info.json"):
        claim_folder = info_file.parent
        for file in claim_folder.iterdir():
            if file.is_file() and file.suffix.lower() in ['.pdf', '.jpg', '.png', '.docx']:
                test_files.append(file)
                if len(test_files) >= 3:
                    break
        if len(test_files) >= 3:
            break
    
    if not test_files:
        print("未找到测试文件")
        return
    
    for file in test_files:
        print(f"\n处理文件: {file.name}")
        result = processor.process_file(file)
        print(f"  类型: {result.get('file_type')}")
        print(f"  成功: {result.get('success')}")
        if result.get('success'):
            print(f"  内容类型: {result.get('content_type')}")
            if result.get('content_type') == 'text':
                print(f"  文本长度: {len(result.get('content', ''))} 字符")
            else:
                print(f"  Base64长度: {len(result.get('content', ''))} 字符")
        else:
            print(f"  错误: {result.get('error')}")
    
    print("\n" + "=" * 60)
    print("测试完成!")


if __name__ == "__main__":
    test_document_processor()
