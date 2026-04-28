#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
隐私脱敏模块
对敏感信息进行脱敏处理
"""

import re
from typing import Dict, Any


class PrivacyMasker:
    """隐私脱敏器"""
    
    # 脱敏规则
    MASKING_RULES = {
        'id_number': {
            'pattern': r'(\d{2})\d{14}(\d{2})',
            'replacement': r'\1**************\2',
            'description': '身份证号'
        },
        'phone': {
            'pattern': r'(\d{3})\d{4}(\d{4})',
            'replacement': r'\1****\2',
            'description': '手机号'
        },
        'bank_card': {
            'pattern': r'(\d{4})\d{8,12}(\d{4})',
            'replacement': r'\1****\2',
            'description': '银行卡号'
        },
        'email': {
            'pattern': r'(\w{1,3})\w+(@\w+\.\w+)',
            'replacement': r'\1***\2',
            'description': '邮箱'
        },
        'name': {
            'pattern': r'(姓名|被保险人|申请人)[:：\s]*([^\s\n]{2,4})',
            'replacement': lambda m: f"{m.group(1)}: {m.group(2)[0]}{'*' * (len(m.group(2)) - 1)}",
            'description': '姓名'
        }
    }
    
    def __init__(self):
        self.masked_count = {}
    
    def mask_text(self, text: str) -> str:
        """
        对文本进行脱敏
        
        Args:
            text: 原始文本
        
        Returns:
            脱敏后的文本
        """
        if not text:
            return text
        
        masked_text = text
        self.masked_count = {}
        
        for rule_name, rule in self.MASKING_RULES.items():
            pattern = rule['pattern']
            replacement = rule['replacement']
            
            # 统计匹配次数
            matches = re.findall(pattern, masked_text)
            if matches:
                self.masked_count[rule_name] = len(matches)
            
            # 执行替换
            if callable(replacement):
                masked_text = re.sub(pattern, replacement, masked_text)
            else:
                masked_text = re.sub(pattern, replacement, masked_text)
        
        return masked_text
    
    def mask_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        对字典数据进行脱敏
        
        Args:
            data: 原始数据字典
        
        Returns:
            脱敏后的数据字典
        """
        if not isinstance(data, dict):
            return data
        
        masked_data = {}
        
        for key, value in data.items():
            if isinstance(value, str):
                masked_data[key] = self.mask_text(value)
            elif isinstance(value, dict):
                masked_data[key] = self.mask_dict(value)
            elif isinstance(value, list):
                masked_data[key] = [
                    self.mask_dict(item) if isinstance(item, dict)
                    else self.mask_text(item) if isinstance(item, str)
                    else item
                    for item in value
                ]
            else:
                masked_data[key] = value
        
        return masked_data
    
    def mask_ocr_result(self, ocr_result: Dict) -> Dict:
        """
        对OCR识别结果进行脱敏
        
        Args:
            ocr_result: OCR识别结果
        
        Returns:
            脱敏后的OCR结果
        """
        masked_result = ocr_result.copy()
        
        # 脱敏文本内容
        if 'text' in masked_result:
            masked_result['text'] = self.mask_text(masked_result['text'])
        
        # 脱敏关键信息
        if 'key_info' in masked_result:
            masked_result['key_info'] = self.mask_dict(masked_result['key_info'])
        
        # 脱敏词语列表
        if 'words' in masked_result:
            for word in masked_result['words']:
                if 'words' in word:
                    word['words'] = self.mask_text(word['words'])
        
        return masked_result
    
    def get_masking_report(self) -> Dict:
        """
        获取脱敏报告
        
        Returns:
            {
                'total_masked': 10,
                'by_type': {
                    'id_number': 2,
                    'phone': 3,
                    'bank_card': 1,
                    'name': 4
                }
            }
        """
        return {
            'total_masked': sum(self.masked_count.values()),
            'by_type': self.masked_count.copy()
        }


def test_privacy_masking():
    """测试脱敏功能"""
    print("测试隐私脱敏功能...")
    print("=" * 60)
    
    masker = PrivacyMasker()
    
    # 测试文本脱敏
    test_text = """
    理赔申请表
    姓名: 张三
    身份证号: 420626198703180025
    手机号: 13812345678
    银行卡号: 6222021234567890123
    邮箱: zhangsan@example.com
    申请金额: 1600元
    """
    
    print("\n原始文本:")
    print(test_text)
    
    masked_text = masker.mask_text(test_text)
    print("\n脱敏后文本:")
    print(masked_text)
    
    # 脱敏报告
    report = masker.get_masking_report()
    print("\n脱敏报告:")
    print(f"总共脱敏: {report['total_masked']} 处")
    for rule_name, count in report['by_type'].items():
        print(f"  - {rule_name}: {count} 处")
    
    # 测试字典脱敏
    print("\n" + "=" * 60)
    print("测试字典脱敏:")
    
    test_dict = {
        'name': '李四',
        'id_number': '110106200502255720',
        'phone': '13987654321',
        'claim_info': {
            'amount': '2000',
            'description': '被保险人王五的行李丢失'
        }
    }
    
    print("\n原始数据:")
    print(test_dict)
    
    masked_dict = masker.mask_dict(test_dict)
    print("\n脱敏后数据:")
    print(masked_dict)
    
    print("\n" + "=" * 60)
    print("测试完成!")


if __name__ == "__main__":
    test_privacy_masking()
