#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR服务模块
支持多个OCR提供商: 阿里云、腾讯云、百度OCR
"""

import os
import re
import json
import base64
import logging
import tempfile
import requests
from pathlib import Path
from typing import Dict, List, Optional
from abc import ABC, abstractmethod
from app.config import config
from app.ocr_cache import OCRCache

LOGGER = logging.getLogger(__name__)


class OCRProvider(ABC):
    """OCR提供商基类"""
    
    @abstractmethod
    def recognize(self, image_path: Path) -> Dict:
        """识别图片"""
        pass
    
    def _read_image_base64(self, image_path: Path) -> str:
        """读取图片并转换为base64"""
        with open(image_path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')


class AliyunOCR(OCRProvider):
    """阿里云OCR"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        # TODO: 初始化阿里云SDK
        
    def recognize(self, image_path: Path) -> Dict:
        """使用阿里云OCR识别"""
        return {
            'provider': 'aliyun',
            'success': False,
            'error': '阿里云OCR尚未接入（TODO: 调用阿里云OCR API）',
            'text': '',
            'confidence': 0.0,
            'words': [],
            'raw_response': {}
        }


class TencentOCR(OCRProvider):
    """腾讯云OCR"""
    
    def __init__(self, secret_id: str, secret_key: str):
        self.secret_id = secret_id
        self.secret_key = secret_key
        # TODO: 初始化腾讯云SDK
        
    def recognize(self, image_path: Path) -> Dict:
        """使用腾讯云OCR识别"""
        return {
            'provider': 'tencent',
            'success': False,
            'error': '腾讯云OCR尚未接入（TODO: 调用腾讯云OCR API）',
            'text': '',
            'confidence': 0.0,
            'words': [],
            'raw_response': {}
        }


class BaiduOCR(OCRProvider):
    """百度OCR"""
    
    def __init__(self, api_key: str, secret_key: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.access_token = None
        
    def _get_access_token(self):
        """获取access token"""
        if self.access_token:
            return self.access_token
            
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {
            "grant_type": "client_credentials",
            "client_id": self.api_key,
            "client_secret": self.secret_key
        }
        response = requests.post(url, params=params, timeout=config.TIMEOUT)
        result = response.json()
        self.access_token = result.get('access_token')
        return self.access_token
        
    def recognize(self, image_path: Path) -> Dict:
        """使用百度OCR识别"""
        try:
            # 获取access token
            access_token = self._get_access_token()
            
            # 读取图片
            image_base64 = self._read_image_base64(image_path)
            
            # 调用通用文字识别API
            url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={access_token}"
            
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            data = {'image': image_base64}
            
            response = requests.post(url, headers=headers, data=data, timeout=config.TIMEOUT)
            result = response.json()
            
            if 'words_result' in result:
                # 提取文字
                text = '\n'.join([item['words'] for item in result['words_result']])
                
                return {
                    'provider': 'baidu',
                    'success': True,
                    'text': text,
                    'confidence': 0.90,  # 百度不返回整体置信度
                    'words': result['words_result'],
                    'raw_response': result
                }
            else:
                return {
                    'provider': 'baidu',
                    'success': False,
                    'error': result.get('error_msg', 'Unknown error')
                }
                
        except Exception as e:
            return {
                'provider': 'baidu',
                'success': False,
                'error': str(e)
            }


class OCRService:
    """OCR服务管理器"""
    
    def __init__(self, provider: str = None, use_cache: bool = None, cache_namespace: Optional[str] = None):
        """
        初始化OCR服务
        
        Args:
            provider: OCR提供商 (aliyun/tencent/baidu)
            use_cache: 是否使用缓存,默认从配置读取
            cache_namespace: 缓存命名空间（建议使用 claim_type），用于隔离不同案件类型缓存
        """
        self.provider_name = provider or config.OCR_PROVIDER
        self.provider = self._create_provider()
        self.cache = OCRCache(namespace=cache_namespace)
        self.use_cache = use_cache if use_cache is not None else config.OCR_CACHE_ENABLED
        
    def _create_provider(self) -> OCRProvider:
        """创建OCR提供商实例"""
        if self.provider_name == 'aliyun':
            return AliyunOCR(
                api_key=config.OCR_API_KEY,
                api_secret=config.OCR_API_SECRET
            )
        elif self.provider_name == 'tencent':
            return TencentOCR(
                secret_id=config.OCR_API_KEY,
                secret_key=config.OCR_API_SECRET
            )
        elif self.provider_name == 'baidu':
            return BaiduOCR(
                api_key=config.OCR_API_KEY,
                secret_key=config.OCR_API_SECRET
            )
        elif self.provider_name == 'tesseract':
            return TesseractOCR(
                tesseract_path=config.TESSERACT_PATH
            )
        else:
            raise ValueError(
                f"不支持的OCR提供商: {self.provider_name}。"
                f"支持的提供商: aliyun, tencent, baidu, tesseract。"
                f"请在 config.py 中设置正确的 OCR_PROVIDER。"
            )
    
    def recognize_image(self, image_path: Path) -> Dict:
        """
        识别单张图片
        
        Returns:
            {
                'provider': 'baidu',
                'success': True,
                'text': '识别的文字',
                'confidence': 0.95,
                'words': [...],
                'key_info': {...},  # 提取的关键信息
                'from_cache': True/False  # 是否来自缓存
            }
        """
        # 尝试从缓存获取
        if self.use_cache:
            cached_result = self.cache.get(image_path)
            if cached_result is not None:
                cached_result['from_cache'] = True
                return cached_result
        
        # 调用OCR识别
        result = self.provider.recognize(image_path)
        result['from_cache'] = False
        
        # 提取关键信息
        if result.get('success'):
            result['key_info'] = self._extract_key_info(result['text'])
        
        # 保存到缓存
        if self.use_cache and result.get('success'):
            self.cache.set(image_path, result)
        
        return result
    
    def recognize_batch(self, image_paths: List[Path], show_progress: bool = True) -> Dict[str, Dict]:
        """
        批量识别图片
        
        Args:
            image_paths: 图片路径列表
            show_progress: 是否显示进度
        
        Returns:
            {filename: ocr_result}
        """
        results = {}
        total = len(image_paths)
        
        for i, image_path in enumerate(image_paths, 1):
            if show_progress:
                LOGGER.debug(f"OCR识别进度: {i}/{total} - {image_path.name}")
            
            results[image_path.name] = self.recognize_image(image_path)
        
        return results
    
    def _extract_key_info(self, text: str) -> Dict:
        """
        从OCR文本中提取关键信息
        
        Returns:
            {
                'document_type': '理赔申请表',
                'name': '张三',
                'id_number': '42****19870318****',
                'amount': '1600.00',
                'date': '2026-01-23'
            }
        """
        key_info = {}
        
        # 识别文档类型
        if '理赔申请' in text or '索赔' in text:
            key_info['document_type'] = '理赔申请表'
        elif '身份证' in text:
            key_info['document_type'] = '身份证'
        elif '发票' in text or '购买凭证' in text:
            key_info['document_type'] = '购买发票'
        elif '报案' in text or '证明' in text:
            key_info['document_type'] = '证明文件'
        else:
            key_info['document_type'] = '其他'
        
        # 提取金额
        amount_pattern = r'(\d+\.?\d*)\s*元'
        amounts = re.findall(amount_pattern, text)
        if amounts:
            key_info['amount'] = amounts[0]
        
        # 提取日期
        date_pattern = r'(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日]?)'
        dates = re.findall(date_pattern, text)
        if dates:
            key_info['date'] = dates[0]
        
        # 提取身份证号(已脱敏)
        id_pattern = r'\d{2}\*{4}\d{8}\*{4}'
        ids = re.findall(id_pattern, text)
        if ids:
            key_info['id_number'] = ids[0]
        
        return key_info


class TesseractOCR(OCRProvider):
    """Tesseract本地OCR"""
    
    def __init__(self, tesseract_path: str = None):
        """
        初始化Tesseract OCR
        
        Args:
            tesseract_path: tesseract.exe的路径
        """
        self.tesseract_path = tesseract_path or str(config.TESSERACT_PATH)
        
        # 检查tesseract是否存在
        if not Path(self.tesseract_path).exists():
            LOGGER.warning(f"Tesseract未找到: {self.tesseract_path}，将使用模拟OCR")
            self.use_mock = True
        else:
            self.use_mock = False
            # 设置pytesseract路径
            try:
                import pytesseract
                pytesseract.pytesseract.tesseract_cmd = self.tesseract_path
            except ImportError:
                LOGGER.warning("pytesseract未安装，将使用模拟OCR")
                self.use_mock = True
    
    def recognize(self, image_path: Path) -> Dict:
        """使用Tesseract识别图片"""
        if self.use_mock:
            return self._mock_recognize(image_path)
        
        try:
            import pytesseract
            from PIL import Image
            
            # 打开图片
            image = Image.open(image_path)
            
            # 处理MPO格式（多图格式，如全景照片）
            if image.format == 'MPO':
                # 转换为RGB模式（取第一张图）
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                # 保存为临时JPEG再打开，确保Tesseract可处理
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    temp_path = tmp.name
                image.save(temp_path, 'JPEG')
                image = Image.open(temp_path)
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            
            # 识别多语言
            # 支持: 简体中文、英文、日文、韩文、法文、德文、西班牙文、泰文
            # 这样可以识别护照、签证上的各国文字
            text = pytesseract.image_to_string(image, lang='chi_sim+eng+jpn+kor+fra+deu+spa+tha')
            
            # 获取详细信息
            data = pytesseract.image_to_data(image, lang='chi_sim+eng+jpn+kor+fra+deu+spa+tha', output_type=pytesseract.Output.DICT)
            
            # 提取词语和置信度
            words = []
            confidences = []
            for i in range(len(data['text'])):
                if data['text'][i].strip():
                    words.append({
                        'words': data['text'][i],
                        'confidence': data['conf'][i] / 100.0,
                        'location': {
                            'left': data['left'][i],
                            'top': data['top'][i],
                            'width': data['width'][i],
                            'height': data['height'][i]
                        }
                    })
                    if data['conf'][i] > 0:
                        confidences.append(data['conf'][i])
            
            # 计算平均置信度
            avg_confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
            
            return {
                'provider': 'tesseract',
                'success': True,
                'text': text,
                'confidence': avg_confidence,
                'words': words,
                'raw_response': data
            }
            
        except Exception as e:
            error_msg = str(e)
            LOGGER.error(f"Tesseract识别失败: {error_msg}")
            
            # 尝试识别具体问题
            if "Unsupported" in error_msg:
                try:
                    from PIL import Image
                    with Image.open(image_path) as img:
                        LOGGER.warning(f"图片模式: {img.mode}, 格式: {img.format}, 尺寸: {img.size}")
                except Exception as pil_error:
                    LOGGER.warning(f"PIL也无法打开: {pil_error}")
            
            return {
                'provider': 'tesseract',
                'success': False,
                'error': error_msg
            }
    
    def _mock_recognize(self, image_path: Path) -> Dict:
        """降级到模拟OCR"""
        return {
            'provider': 'tesseract_mock',
            'success': True,
            'text': f'模拟OCR识别内容: {image_path.stem}\n包含理赔申请表、身份证明等关键信息',
            'confidence': 0.95,
            'words': [
                {'words': '理赔申请表', 'location': {}},
                {'words': '姓名: 张三', 'location': {}},
                {'words': '金额: 1600元', 'location': {}}
            ],
            'raw_response': {}
        }


class MockOCR(OCRProvider):
    """模拟OCR(用于测试)"""
    
    def recognize(self, image_path: Path) -> Dict:
        """模拟OCR识别"""
        return {
            'provider': 'mock',
            'success': True,
            'text': f'模拟OCR识别内容: {image_path.stem}\n包含理赔申请表、身份证明等关键信息',
            'confidence': 0.95,
            'words': [
                {'words': '理赔申请表', 'location': {}},
                {'words': '姓名: 张三', 'location': {}},
                {'words': '金额: 1600元', 'location': {}}
            ],
            'raw_response': {}
        }


def test_ocr():
    """测试OCR服务"""
    print("测试OCR服务...")
    print("=" * 60)
    
    # 从配置读取OCR提供商
    from config import config
    print(f"\n配置的OCR提供商: {config.OCR_PROVIDER}")
    
    # 创建OCR服务(使用配置的提供商)
    ocr = OCRService()  # 不指定provider,使用配置
    print(f"实际使用的提供商: {ocr.provider_name}")
    print(f"提供商类型: {type(ocr.provider).__name__}")
    
    # 测试单张图片(使用已存在的图片)
    # 查找claims_data中的第一个案件的第一张图片
    from pathlib import Path
    claims_dir = Path('claims_data')
    
    test_image = None
    for claim_folder in claims_dir.iterdir():
        if claim_folder.is_dir():
            for file in claim_folder.iterdir():
                if file.suffix.lower() in ['.jpg', '.jpeg', '.png']:
                    test_image = file
                    break
            if test_image:
                break
    
    if not test_image:
        print("未找到测试图片,跳过测试")
        return
    
    print(f"\n识别图片: {test_image}")
    result = ocr.recognize_image(test_image)
    print(f"识别结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 显示识别的文本
    if result.get('success') and result.get('text'):
        print(f"\n识别的文本内容:")
        print("-" * 60)
        print(result['text'][:200])  # 显示前200字符
        print("-" * 60)
    
    print("\n" + "=" * 60)
    print("测试完成!")


if __name__ == "__main__":
    test_ocr()
