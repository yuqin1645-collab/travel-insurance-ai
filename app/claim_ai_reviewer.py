#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI理赔审核系统 - 使用大模型进行智能审核
将审核拆分成多个子prompt,提高准确性
支持异步并发处理,大幅提升速度
"""

import os
import json
import re
import asyncio
import aiohttp
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from app.openrouter_client import OpenRouterClient, TaskDifficulty
from app.prompt_loader import prompt_loader
from app.config import config
from app.ocr_service import OCRService
from app.privacy_masking import PrivacyMasker
from app.document_processor import DocumentProcessor
from app.gemini_vision_client import GeminiVisionClient
from app.vision_preprocessor import prepare_attachments_for_claim
from app.modules.registry import DEFAULT_REGISTRY
from app.policy_terms_registry import POLICY_TERMS
from app.modules.baggage_damage.extractors import (
    extract_purchase_amount_and_date,
    extract_third_party_compensation_amount,
)
from app.modules.baggage_damage.pipeline import review_baggage_damage_async
from app.modules.baggage_damage.stages import (
    ai_calculate_compensation_async,
    ai_check_coverage_async,
    ai_check_materials_async,
    ai_judge_accident_async,
    extract_section,
    summarize_ocr_results,
)
from app.modules.flight_delay.pipeline import review_flight_delay_async
from app.logging_utils import LOGGER, log_extra as _log_extra


"""
日志工具已迁移到 app.logging_utils，避免循环依赖：
- claim_ai_reviewer -> ocr_service -> ocr_cache -> claim_ai_reviewer
"""


class AIClaimReviewer:
    """AI理赔审核器 - 使用OpenRouter API"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        初始化AI审核器
        
        Args:
            api_key: OpenRouter API密钥,如果不提供则从配置读取
        """
        self.client = OpenRouterClient(api_key=api_key)
        self.prompt_loader = prompt_loader
        self.privacy_masker = PrivacyMasker()
        self.doc_processor = DocumentProcessor()  # 新增文档处理器
        if config.USE_QWEN_VISION:
            _vision_api_key = config.DASHSCOPE_API_KEY
            _vision_provider = 'dashscope'
        else:
            _vision_api_key = config.OPENROUTER_API_KEY
            _vision_provider = 'openrouter'
        self.vision_client = GeminiVisionClient(api_key=_vision_api_key, provider=_vision_provider)
        # 当前所有案件默认为随身财产模块；未来可按案件类型路由
        self.module = DEFAULT_REGISTRY.get("baggage_damage")
        self.prompt_namespace = self.module.get_context().prompt_namespace
        self.ocr_service = OCRService(cache_namespace=self.module.get_context().claim_type)

    def set_claim_type(self, claim_type: str) -> None:
        """根据 claim_type 切换模块上下文（prompts/条款/OCR缓存命名空间）。"""
        self.module = DEFAULT_REGISTRY.get(claim_type)
        ctx = self.module.get_context()
        self.prompt_namespace = ctx.prompt_namespace
        self.ocr_service = OCRService(cache_namespace=ctx.claim_type)

    async def _ai_flight_delay_parse_async(
        self,
        claim_info: Dict,
        free_text: str,
        session: 'aiohttp.ClientSession',
    ) -> Dict:
        prompt = self.prompt_loader.format(
            "01_data_parse_and_timezone",
            namespace=self.prompt_namespace,
            claim_info_json=json.dumps(claim_info, ensure_ascii=False),
            free_text=free_text or "",
        )
        return await self.client.chat_completion_json_async(
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.MEDIUM,
            session=session,
        )

    async def _ai_flight_delay_vision_extract_async(
        self,
        attachment_paths: list,
        claim_info: Dict,
        session: 'aiohttp.ClientSession',
    ) -> Dict:
        """
        Stage 0.5：使用 Vision 模型从图片/PDF中抽取关键延误字段。
        返回与 flight_delay_parse 相同 schema 的子集（unknown 表示未识别）。
        降级：若无附件或调用失败，返回空 dict。
        """
        if not attachment_paths:
            return {}
        prompt = self.prompt_loader.format(
            "00_vision_extract",
            namespace=self.prompt_namespace,
            claim_info_json=json.dumps(claim_info, ensure_ascii=False, indent=2),
        )
        return await self.vision_client.review_materials_with_vision(
            material_files=attachment_paths,
            prompt=prompt,
            session=session,
        )

    async def _ai_flight_delay_audit_async(
        self,
        claim_info: Dict,
        parsed: Dict,
        policy_terms_excerpt: str,
        session: 'aiohttp.ClientSession',
        payout_json: Optional[Dict] = None,
    ) -> Dict:
        prompt = self.prompt_loader.format(
            "02_audit_decision",
            namespace=self.prompt_namespace,
            parsed_json=json.dumps(parsed, ensure_ascii=False),
            policy_terms_excerpt=policy_terms_excerpt or "",
            claim_info_json=json.dumps(claim_info, ensure_ascii=False),
            payout_json=json.dumps(payout_json, ensure_ascii=False) if payout_json else "{}",
        )
        return await self.client.chat_completion_json_async(
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.MEDIUM,
            session=session,
        )
    
    # ==================== 公共小工具 ====================
    def _extract_earliest_travel_date_from_ocr(self, ocr_results: Dict) -> Optional[datetime]:
        """
        从OCR文本中粗略提取“最早出行日期”(用于判断是否出境后才投保).
        
        仅在包含出入境/移民管理局/行程单等关键词的文件上做日期解析,避免误判。
        """
        candidates: List[datetime] = []
        
        if not ocr_results:
            return None
        
        for filename, result in ocr_results.items():
            text = result.get("text") or ""
            if not text:
                continue
            
            # 只在疑似行程/出入境记录里搜索
            keywords = [
                "出入境记录", "国家移民管理局", "出入境记录查询结果",
                "电子客票行程单", "行程单", "航班", "登机牌", "boarding pass", "Boarding Pass",
                "出入境", "入境", "出境"
            ]
            text_lower = text.lower()
            if not any(k.lower() in text_lower for k in keywords):
                continue
            
            # 上下文关键字:
            # - 允许(更像“出行日期”): 起飞/出发/Departure/Flight date/航班日期/行程
            # - 排除(常见误判来源): 签证有效期/签发日期/事故发生日期/理赔申请日期等
            travel_context_keywords = [
                "起飞", "出发", "出境", "入境", "departure", "flight", "航班", "行程", "date"
            ]
            exclude_context_keywords = [
                "签证", "有效期", "签发", "visa", "visto", "valid", "until", "from",
                "事故发生", "incident", "date of incident", "理赔", "索赔", "claim"
            ]

            # 匹配日期(允许 2026-02-20 / 2026/02/20 / 2026年02月20日)
            for m in re.finditer(r"20\d{2}[年\-\/\.]\d{1,2}[月\-\/\.]\d{1,2}", text):
                raw = m.group(0)
                # 归一化为 yyyy-mm-dd
                cleaned = (
                    raw.replace("年", "-")
                       .replace("月", "-")
                       .replace("日", "")
                       .replace("/", "-")
                       .replace(".", "-")
                )
                # 上下文过滤：避免把签证有效期等日期当作“出行日期”
                left = max(0, m.start() - 30)
                right = min(len(text), m.end() + 30)
                ctx = text[left:right]
                ctx_lower = ctx.lower()

                has_travel_ctx = any(k.lower() in ctx_lower for k in travel_context_keywords)
                has_exclude_ctx = any(k.lower() in ctx_lower for k in exclude_context_keywords)

                # 如果缺少任何“出行相关上下文”，直接跳过（避免护照页/签证页任意日期被采纳）
                if not has_travel_ctx:
                    continue
                # 若命中排除上下文，则仅当同时包含“出入境章”语义时才保留
                if has_exclude_ctx and not (("出境" in ctx) or ("入境" in ctx)):
                    continue
                try:
                    d = datetime.strptime(cleaned, "%Y-%m-%d")
                    candidates.append(d)
                except Exception:
                    continue
        
        if not candidates:
            return None
        return min(candidates)
    
    def review_claim_with_ai(self, claim_folder: Path, policy_terms: str) -> Dict:
        """使用AI进行完整理赔审核"""
        
        # 读取案件信息
        claim_info = self._load_claim_info(claim_folder)
        forceid = claim_info.get('forceid', 'unknown')
        
        LOGGER.info("=" * 60, extra=_log_extra(forceid=forceid, stage="runner"))
        LOGGER.info(f"开始AI审核案件: {forceid}", extra=_log_extra(forceid=forceid, stage="runner"))
        LOGGER.info("=" * 60, extra=_log_extra(forceid=forceid, stage="runner"))
        
        # 收集所有材料的OCR结果
        LOGGER.info("[准备] OCR识别材料...", extra=_log_extra(forceid=forceid, stage="ocr"))
        ocr_results = self._ocr_all_materials(claim_folder)
        LOGGER.info(f"识别了 {len(ocr_results)} 个文件", extra=_log_extra(forceid=forceid, stage="ocr"))
        
        # 子任务1: 保障责任判断
        LOGGER.info("[阶段1] 保障责任检查...", extra=_log_extra(forceid=forceid, stage="coverage"))
        coverage_result = self._ai_check_coverage(claim_info, policy_terms)
        
        # 子任务2: 材料完整性审核
        LOGGER.info("[阶段2] 材料审核...", extra=_log_extra(forceid=forceid, stage="materials"))
        material_result = self._ai_check_materials(claim_info, ocr_results, policy_terms)
        
        # 子任务3: 事故判责
        LOGGER.info("[阶段3] 事故判责...", extra=_log_extra(forceid=forceid, stage="accident"))
        accident_result = self._ai_judge_accident(
            claim_info, ocr_results, policy_terms
        )
        
        # 子任务4: 赔偿金额核算
        LOGGER.info("[阶段4] 赔偿核算...", extra=_log_extra(forceid=forceid, stage="compensation"))
        compensation_result = self._ai_calculate_compensation(
            claim_info, ocr_results, policy_terms, coverage_result
        )
        
        # 子任务5: 最终汇总
        LOGGER.info("[阶段5] 生成最终审核结论...", extra=_log_extra(forceid=forceid, stage="final"))
        
        # 直接生成最终结果(不调用AI)
        forceid = claim_info.get('forceid', 'unknown')
        
        # 判断各个核对点
        key_conclusions = []
        
        # 1. 保障责任核对
        coverage_eligible = coverage_result.get('has_coverage', False) and \
                          coverage_result.get('in_coverage_period', False) and \
                          not coverage_result.get('exceeds_limit', True)
        key_conclusions.append({
            "checkpoint": "保障责任核对",
            "Eligible": "Y" if coverage_eligible else "N",
            "Remark": coverage_result.get('reason', '')
        })
        
        # 2. 材料完整性核对
        material_eligible = material_result.get('is_complete', False)
        key_conclusions.append({
            "checkpoint": "材料完整性核对",
            "Eligible": "Y" if material_eligible else "N",
            "Remark": material_result.get('reason', '')
        })
        
        # 3. 保障范围核对
        coverage_scope_eligible = accident_result.get('is_covered', False)
        key_conclusions.append({
            "checkpoint": "保障范围核对",
            "Eligible": "Y" if coverage_scope_eligible else "N",
            "Remark": accident_result.get('coverage_reason', '')
        })
        
        # 4. 除外责任核对
        exclusion_eligible = not accident_result.get('is_excluded', True)
        key_conclusions.append({
            "checkpoint": "除外责任核对",
            "Eligible": "Y" if exclusion_eligible else "N",
            "Remark": accident_result.get('exclusion_reason', '未触发除外责任')
        })
        
        # 5. 赔偿金额核对
        compensation_eligible = compensation_result.get('final_amount', 0) > 0
        key_conclusions.append({
            "checkpoint": "赔偿金额核对",
            "Eligible": "Y" if compensation_eligible else "N",
            "Remark": compensation_result.get('calculation_steps', '')
        })
        
        # 判断是否需要补件
        needs_additional = material_result.get('needs_manual_review', False) or \
                          not material_result.get('is_complete', False)
        
        # 生成最终结论
        if needs_additional:
            missing = material_result.get('missing_materials', [])
            remark = f"需要补充材料: {', '.join(missing)}" if missing else "需要人工审核"
        elif not coverage_eligible:
            remark = "拒赔: 不符合保障责任要求"
        elif not coverage_scope_eligible:
            remark = "拒赔: 不属于保障范围"
        elif not exclusion_eligible:
            remark = f"拒赔: {accident_result.get('exclusion_reason', '触发除外责任')}"
        elif compensation_eligible:
            final_amount = compensation_result.get('final_amount', 0)
            remark = f"审核通过,同意赔付{final_amount}元"
        else:
            remark = "拒赔: 赔偿金额为0"
        
        final_result = {
            "forceid": forceid,
            "Remark": remark,
            "IsAdditional": "Y" if needs_additional else "N",
            "KeyConclusions": key_conclusions
        }
        
        LOGGER.info(f"最终结论: {final_result.get('Remark', '')}", extra=_log_extra(forceid=forceid, stage="final"))
        
        return final_result
    
    def _ai_check_coverage(self, claim_info: Dict, policy_terms: str) -> Dict:
        """
        子Prompt 1: 保障责任判断
        难度: SIMPLE (信息提取和简单规则匹配)
        """
        # 使用prompt加载器加载并格式化prompt
        prompt = self.prompt_loader.format(
            '01_coverage_check',
            namespace=self.prompt_namespace,
            policy_no=claim_info.get('PolicyNo', ''),
            product_name=claim_info.get('Product_Name', ''),
            benefit_name=claim_info.get('BenefitName', ''),
            insured_amount=claim_info.get('Insured_Amount', ''),
            remaining_coverage=claim_info.get('Remaining_Coverage', ''),
            effective_date=claim_info.get('Effective_Date', ''),
            expiry_date=claim_info.get('Expiry_Date', ''),
            accident_date=claim_info.get('Date_of_Accident', ''),
            claim_amount=claim_info.get('Amount', ''),
            policy_terms_excerpt=policy_terms[:500]
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = self.client.chat_completion_json(
                messages=messages,
                difficulty=TaskDifficulty.SIMPLE
            )
            LOGGER.info(
                f"模型返回: {result.get('reason', '')}",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="coverage"),
            )
            return result
        except Exception as e:
            LOGGER.warning(
                f"API调用失败: {e}",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="coverage"),
            )
            # 降级到规则引擎
            return {
                "has_coverage": False,
                "in_coverage_period": False,
                "exceeds_limit": True,
                "reason": f"API调用失败,需要人工审核: {str(e)}",
                "single_limit": float(claim_info.get('Insured_Amount', 0)),
                "remaining_amount": float(claim_info.get('Remaining_Coverage', 0))
            }
    
    def _ai_check_materials(
        self, 
        claim_info: Dict, 
        ocr_results: Dict,
        policy_terms: str
    ) -> Dict:
        """
        子Prompt 2: 材料完整性审核(同步入口)
        现在统一走 vision 分批看图流程, 不再使用OCR长文本。
        """
        import aiohttp
        import asyncio

        async def _run() -> Dict:
            async with aiohttp.ClientSession(trust_env=True) as session:
                return await self._ai_check_materials_async(
                    claim_info, ocr_results, policy_terms, session
                )

        return asyncio.run(_run())

    
    def _ai_judge_accident(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str
    ) -> Dict:
        """
        子Prompt 3: 事故判责
        """
        ocr_summary = self._summarize_ocr_results(ocr_results)
        
        prompt = f"""
你是一位专业的保险理赔审核员,请对事故进行判责。

## 案件信息
- 事故描述: {claim_info.get('Description_of_Accident')}
- 出险日期: {claim_info.get('Date_of_Accident')}
- 被保险人: {claim_info.get('Insured_And_Policy')}

## 材料识别信息
{json.dumps(ocr_summary, ensure_ascii=False, indent=2)}

## 保险条款
### 一、权益内容(保障范围)
{policy_terms[policy_terms.find('一、 权益内容'):policy_terms.find('二、 不属于权益范围')]}

### 二、不属于权益范围的情形(除外责任)
{policy_terms[policy_terms.find('二、 不属于权益范围'):policy_terms.find('三、 权益人义务')]}

### 九、释义
{policy_terms[policy_terms.find('九、 释义'):policy_terms.find('十、 服务区域')]}

## 审核流程
1. 先通过理赔材料/申请书识别实际事故情况,优先以客观材料为准
2. 匹配条款保障范围(参考名词解释),判断是否符合
3. 核查是否触发除外责任

## 重点除外责任
- 贵重物品、电子产品(手机、电脑、iPad等)
- 现金、银行卡、身份证、护照等证件
- 原因不明的损失或神秘失踪
- 未尽看管义务导致的损失
- 正常磨损、折旧
- 放置在无人看管的公共场所或车辆(无暴力痕迹)

请以JSON格式返回:
{{
    "accident_type": "事故类型(盗窃/抢劫/承运人责任/意外损坏等)",
    "is_covered": true/false,
    "coverage_reason": "是否属于保障范围的判断依据",
    "is_excluded": true/false,
    "exclusion_reason": "触发的除外责任条款(如有)",
    "final_judgment": "最终判责结论",
    "reason": "详细说明"
}}
"""
        
        # TODO: 调用大模型API
        
        # 模拟返回
        return {
            "accident_type": "承运人责任-行李损坏",
            "is_covered": True,
            "coverage_reason": "属于条款第一条保障范围:承运人责任导致的随身财产损坏",
            "is_excluded": False,
            "exclusion_reason": "",
            "final_judgment": "符合理赔条件",
            "reason": "托运行李箱在航空运输过程中损坏,属于承运人责任,符合保障范围且未触发除外责任"
        }
    
    def _ai_calculate_compensation(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str,
        coverage_result: Dict
    ) -> Dict:
        """
        子Prompt 4: 赔偿金额核算
        """
        ocr_summary = self._summarize_ocr_results(ocr_results)
        
        prompt = f"""
你是一位专业的保险理赔核算员,请计算赔偿金额。

## 案件信息
- 申请金额: {claim_info.get('Amount')}元
- 事故日期: {claim_info.get('Date_of_Accident')}
- 剩余保额: {coverage_result['remaining_amount']}元

## 材料识别信息(含购买日期、发票金额等)
{json.dumps(ocr_summary, ensure_ascii=False, indent=2)}

## 赔偿核算规则
1. 按实际现金价值赔付:
   实际现金价值 = 重置费用 × (1 - 每月折旧率 × 购买月数)
   - 每月折旧率: 1%
   - 不足一个月按一个月计算

2. 扣除第三方已赔付金额(如航司、承运人补偿)

3. 成套/配套物品按一件/一套计算
   - 单套/单件赔偿上限: 1000元

4. 不超过剩余保额

## 计算步骤
1. 确定重置费用(原价)
2. 计算购买月数
3. 计算实际现金价值
4. 扣除第三方赔偿
5. 应用单件限额(1000元)
6. 应用总保额限制

请以JSON格式返回:
{{
    "original_value": 原价,
    "purchase_date": "购买日期",
    "depreciation_months": 折旧月数,
    "depreciation_rate": 0.01,
    "actual_cash_value": 实际现金价值,
    "third_party_compensation": 第三方已赔付,
    "after_third_party": 扣除第三方后金额,
    "single_item_limit": 1000,
    "after_item_limit": 应用单件限额后,
    "remaining_coverage": 剩余保额,
    "final_amount": 最终赔付金额,
    "calculation_steps": "详细计算步骤",
    "reason": "核算说明"
}}
"""
        
        # TODO: 调用大模型API
        
        # 模拟返回
        claim_amount = float(claim_info.get('Amount', 0))
        return {
            "original_value": claim_amount,
            "purchase_date": "2025-01-01",
            "depreciation_months": 13,
            "depreciation_rate": 0.01,
            "actual_cash_value": claim_amount * 0.87,
            "third_party_compensation": 0,
            "after_third_party": claim_amount * 0.87,
            "single_item_limit": 1000,
            "after_item_limit": min(claim_amount * 0.87, 1000),
            "remaining_coverage": coverage_result['remaining_amount'],
            "final_amount": min(min(claim_amount * 0.87, 1000), coverage_result['remaining_amount']),
            "calculation_steps": "1. 原价1600元 2. 折旧13个月 3. 实际价值1392元 4. 应用单件限额1000元 5. 最终赔付1000元",
            "reason": "按实际现金价值计算并应用单件限额"
        }
    
    def _load_claim_info(self, claim_folder: Path) -> Dict:
        """加载案件信息"""
        claim_info_file = claim_folder / "claim_info.json"
        with open(claim_info_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _ocr_all_materials(self, claim_folder: Path) -> Dict:
        """
        处理所有材料文件(图片、PDF、DOCX等)
        支持OCR识别和文档提取
        """
        # 收集所有文件
        files = list(claim_folder.glob('*'))
        material_files = [
            f for f in files 
            if f.is_file() 
            and f.name != 'claim_info.json'
            and f.suffix.lower() in ['.jpg', '.jpeg', '.png', '.pdf', '.docx', '.doc']
        ]
        
        if not material_files:
            return {}
        
        # 处理所有文件
        all_results = {}
        
        for file in material_files:
            suffix = file.suffix.lower()
            
            # PDF和DOCX使用文档处理器
            if suffix in ['.pdf', '.docx', '.doc']:
                result = self.doc_processor.process_file(file)
                if result.get('success'):
                    # 转换为OCR结果格式
                    text = result.get('text_content', '') or result.get('content', '')
                    all_results[file.name] = {
                        'provider': 'document_processor',
                        'success': True,
                        'text': text,
                        'confidence': 1.0,
                        'file_type': result['file_type'],
                        'content_type': result.get('content_type'),
                        'key_info': {}
                    }
                else:
                    all_results[file.name] = {
                        'provider': 'document_processor',
                        'success': False,
                        'error': result.get('error', '处理失败'),
                        'file_type': suffix[1:]
                    }
            
            # 图片使用OCR
            elif suffix in ['.jpg', '.jpeg', '.png']:
                result = self.ocr_service.recognize_image(file)
                all_results[file.name] = result
        
        # 对所有结果进行脱敏
        masked_results = {}
        for filename, result in all_results.items():
            if result.get('success') and result.get('text'):
                # 脱敏文本内容
                masked_text = self.privacy_masker.mask_text(result['text'])
                result['text'] = masked_text
                masked_results[filename] = result
            else:
                masked_results[filename] = result
        
        # 打印脱敏报告
        report = self.privacy_masker.get_masking_report()
        if report['total_masked'] > 0:
            LOGGER.info(
                f"脱敏处理: 共{report['total_masked']}处敏感信息",
                extra=_log_extra(stage="ocr"),
            )
        
        return masked_results
    
    def _summarize_ocr_results(self, ocr_results: Dict) -> Dict:
        """汇总OCR结果的关键信息"""
        return summarize_ocr_results(ocr_results)

    
    def _format_materials_for_review(self, ocr_results: Dict) -> str:
        """格式化材料信息供AI审核使用,包含完整文本内容"""
        if not ocr_results:
            return "未提交任何材料文件"
        
        materials_text = f"共提交 {len(ocr_results)} 个文件:\n\n"
        
        for i, (filename, result) in enumerate(ocr_results.items(), 1):
            materials_text += f"## 文件 {i}: {filename}\n"
            
            # 文件类型
            file_type = result.get('file_type', '未知')
            materials_text += f"- 文件类型: {file_type}\n"
            
            # 识别状态
            if result.get('success'):
                confidence = result.get('confidence', 0)
                materials_text += f"- 识别状态: 成功 (置信度: {confidence:.0%})\n"
                
                # 完整文本内容(限制长度)
                text_content = result.get('text', '')
                if text_content:
                    # 每个文件最多1000字符,避免prompt过大
                    max_chars = 1000
                    if len(text_content) > max_chars:
                        materials_text += f"- 文本内容:\n```\n{text_content[:max_chars]}\n```\n"
                        materials_text += f"(内容过长,已截取前{max_chars}字符)\n"
                    else:
                        materials_text += f"- 文本内容:\n```\n{text_content}\n```\n"
                else:
                    materials_text += "- 文本内容: (空)\n"
            else:
                error = result.get('error', '未知错误')
                materials_text += f"- 识别状态: 失败 ({error})\n"
            
            materials_text += "\n"
        
        return materials_text
    
    def _create_rejection(self, reason: str, details: Dict) -> Dict:
        """创建拒赔结果"""
        return {
            'status': 'rejected',
            'reason': reason,
            'details': details,
            'final_decision': f'拒赔: {reason}'
        }
    
    def _create_manual_review(self, reason: str, details: Dict) -> Dict:
        """创建人工审核结果"""
        return {
            'status': 'manual_review',
            'reason': reason,
            'details': details,
            'final_decision': f'转人工审核: {reason}'
        }
    
    # ==================== 异步方法 ====================
    
    async def _ai_check_coverage_async(
        self, 
        claim_info: Dict, 
        policy_terms: str,
        session: 'aiohttp.ClientSession'
    ) -> Dict:
        """异步版本: 保障责任判断"""
        prompt = self.prompt_loader.format(
            '01_coverage_check',
            namespace=self.prompt_namespace,
            policy_no=claim_info.get('PolicyNo', ''),
            product_name=claim_info.get('Product_Name', ''),
            benefit_name=claim_info.get('BenefitName', ''),
            insured_amount=claim_info.get('Insured_Amount', ''),
            remaining_coverage=claim_info.get('Remaining_Coverage', ''),
            effective_date=claim_info.get('Effective_Date', ''),
            expiry_date=claim_info.get('Expiry_Date', ''),
            accident_date=claim_info.get('Date_of_Accident', ''),
            claim_amount=claim_info.get('Amount', ''),
            policy_terms_excerpt=policy_terms[:500]
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = await self.client.chat_completion_json_async(
                messages=messages,
                difficulty=TaskDifficulty.SIMPLE,
                session=session
            )
            return result
        except Exception as e:
            return {
                "has_coverage": False,
                "in_coverage_period": False,
                "exceeds_limit": True,
                "reason": f"API调用失败: {str(e)}",
                "single_limit": float(claim_info.get('Insured_Amount', 0)),
                "remaining_amount": float(claim_info.get('Remaining_Coverage', 0))
            }
    
    async def _ai_check_materials_async(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str,
        session: 'aiohttp.ClientSession'
    ) -> Dict:
        """异步版本: 材料完整性审核 - 统一使用vision分批看图+证据汇总"""
        try:
            claim_folder = Path(claim_info.get("_claim_folder_path", "")) if claim_info.get("_claim_folder_path") else None
            if claim_folder is None:
                # 兜底: 尝试从forceid匹配目录名(仅用于测试脚本)
                forceid = claim_info.get("forceid") or ""
                claim_folder = next(
                    (d for d in config.CLAIMS_DATA_DIR.rglob("*")
                     if d.is_dir() and forceid and forceid in d.name),
                    None
                )
            if claim_folder is None:
                raise RuntimeError("无法定位案件目录(vision模式需要原始材料文件)")

            # 先准备“全量附件”（prepare 内部会做限额筛选，但我们这里需要分批处理全量）
            old_max = getattr(config, "VISION_MAX_ATTACHMENTS", 10)
            try:
                config.VISION_MAX_ATTACHMENTS = 10**9  # type: ignore[attr-defined]
                all_attachments, manifest = prepare_attachments_for_claim(claim_folder=claim_folder)
            finally:
                config.VISION_MAX_ATTACHMENTS = old_max  # type: ignore[attr-defined]

            if not all_attachments:
                # 业务语义：未提交任何材料（不是“模型失败”）
                return {
                    "is_complete": False,
                    "missing_materials": [
                        "理赔申请表",
                        "被保险人身份证正反面",
                        "银行卡（借记卡）",
                        "交通票据（机票/登机牌/行程单等）",
                        "护照照片页/签证页/出入境记录（或电子出入境记录）",
                        "24小时内报案证明或航空公司PIR（含事故经过与损失明细）",
                        "购买凭证（发票/收据/订单截图/购物小票等，如适用）",
                        "损坏物品照片（如适用）"
                    ],
                    "invalid_materials": [],
                    "needs_manual_review": False,
                    "manual_review_reason": "",
                    "reason": "未提交任何可识别的材料文件（图片/PDF），无法完成材料核对"
                }

            # 分批看图提取证据
            batch_size = int(getattr(config, "VISION_MAX_ATTACHMENTS", 10) or 10)
            batch_size = max(1, batch_size)

            merged_present = {
                "claim_form": False,
                "id_card": False,
                "bank_card": False,
                "travel_ticket": False,
                "passport": False,
                "visa_entry_exit": False,
                "baggage_tag": False,
                "pir_or_report": False,
                "purchase_proof": False,
                "damage_photos": False,
                "loss_list": False,
            }
            merged_evidence = {k: [] for k in merged_present.keys()}
            merged_notes = []

            for bi in range(0, len(all_attachments), batch_size):
                batch = all_attachments[bi:bi + batch_size]
                batch_manifest = {
                    "batch_index": bi // batch_size + 1,
                    "batch_total": (len(all_attachments) + batch_size - 1) // batch_size,
                    "attachments": [a.path.name for a in batch],
                    "source_hint": [a.source_file.name for a in batch],
                }
                p = self.prompt_loader.format(
                    "02_material_evidence_vision",
                    namespace=self.prompt_namespace,
                    benefit_name=claim_info.get("BenefitName") or "未知",
                    accident_description=claim_info.get("Description_of_Accident") or "未提供事故描述",
                    accident_date=claim_info.get("Date_of_Accident") or "未知",
                    batch_manifest=json.dumps(batch_manifest, ensure_ascii=False, indent=2),
                )
                batch_res = await self.vision_client.review_materials_with_vision(
                    material_files=[a.path for a in batch],
                    prompt=p,
                    session=session,
                )

                present = batch_res.get("present") or {}
                evidence = batch_res.get("evidence") or {}
                notes = (batch_res.get("notes") or "").strip()
                if notes:
                    merged_notes.append(notes)

                for k in merged_present.keys():
                    if bool(present.get(k)):
                        merged_present[k] = True
                    ev = evidence.get(k) or []
                    if isinstance(ev, list):
                        merged_evidence[k].extend([str(x) for x in ev if x])

            # 去重 evidence
            for k in merged_evidence.keys():
                merged_evidence[k] = sorted(set(merged_evidence[k]))

            evidence_summary = {
                "present": merged_present,
                "evidence": merged_evidence,
                "notes": merged_notes[:10],
                "total_attachments": len(all_attachments),
                "batch_size": batch_size,
            }

            final_prompt = self.prompt_loader.format(
                "02_material_check_evidence",
                namespace=self.prompt_namespace,
                benefit_name=claim_info.get("BenefitName") or "未知",
                accident_description=claim_info.get("Description_of_Accident") or "未提供事故描述",
                accident_date=claim_info.get("Date_of_Accident") or "未知",
                evidence_summary=json.dumps(evidence_summary, ensure_ascii=False, indent=2),
            )

            # 最终结论用常规chat接口即可（文本很短，不会400）
            result = await self.client.chat_completion_json_async(
                messages=[{"role": "user", "content": final_prompt}],
                difficulty=TaskDifficulty.MEDIUM,
                session=session,
            )

            # 兜底后处理：出入境/护照类材料口径（出境登机牌/机票订单任一即可）
            try:
                missing = result.get("missing_materials") or []
                if isinstance(missing, list) and missing:
                    has_travel_ticket = bool(merged_present.get("travel_ticket"))
                    has_passport_like = bool(merged_present.get("passport") or merged_present.get("visa_entry_exit"))
                    if (has_travel_ticket or has_passport_like):
                        new_missing = []
                        for m in missing:
                            ms = str(m)
                            if ("出入境" in ms) or ("护照类" in ms) or ("护照" in ms) or ("签证" in ms):
                                # 只要出现了机票/登机牌/订单等交通票据或护照/签证/出入境记录，就不应再判该项缺失
                                continue
                            new_missing.append(m)
                        result["missing_materials"] = new_missing
                        if not new_missing and not (result.get("invalid_materials") or []) and not bool(result.get("needs_manual_review", False)):
                            result["is_complete"] = True
            except Exception:
                pass

            # 兜底后处理：损失清单不是必需材料（损坏/偷盗/丢失场景均不因缺清单补件）
            try:
                missing = result.get("missing_materials") or []
                if isinstance(missing, list) and missing:
                    result["missing_materials"] = [m for m in missing if "损失清单" not in str(m)]
            except Exception:
                pass

            # 补强后处理：购买凭证/发票用于核定原价，缺失应直接判“需补件”，不要仅给 needs_manual_review
            try:
                if not bool(merged_present.get("purchase_proof")):
                    mm = result.get("missing_materials") or []
                    if not isinstance(mm, list):
                        mm = []
                    tip = "购买凭证/发票/收据（用于核定受损财产原价）"
                    if not any("购买凭证" in str(x) or "发票" in str(x) for x in mm):
                        mm.append(tip)
                    result["missing_materials"] = mm
                    result["is_complete"] = False
                    # 明确是缺件，不是“仅人工核对一致性”
                    result["needs_manual_review"] = False
                    if not (result.get("reason") or ""):
                        result["reason"] = "缺少购买凭证/发票/收据，无法核定原价"
            except Exception:
                pass

            # 透传识别到的材料“是否存在”标记，供主流程做硬门禁
            try:
                result.setdefault("present_flags", merged_present)
            except Exception:
                pass
            return result
        except Exception as e:
            # 系统/调用异常：不应伪装成“缺材料”，而应提示上游转人工
            return {
                "is_complete": False,
                "missing_materials": [],
                "invalid_materials": [],
                "needs_manual_review": True,
                "manual_review_reason": f"材料审核系统异常，需要人工审核：{str(e)[:200]}",
                "reason": "材料审核系统异常，已转人工审核"
            }
        
        try:
            start_idx = policy_terms.find('五、 证明文件')
            end_idx = policy_terms.find('六、 特别说明')
            if start_idx != -1 and end_idx != -1:
                materials_section = policy_terms[start_idx:end_idx]
            else:
                materials_section = ""
        except:
            materials_section = ""
        
        prompt = self.prompt_loader.format(
            '02_material_check',
            namespace=self.prompt_namespace,
            benefit_name=claim_info.get('BenefitName') or '未知',
            accident_description=claim_info.get('Description_of_Accident') or '未提供事故描述',
            accident_date=claim_info.get('Date_of_Accident') or '未知',
            materials_text=materials_text,
            policy_terms_materials=materials_section,
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = await self.client.chat_completion_json_async(
                messages=messages,
                difficulty=TaskDifficulty.MEDIUM,
                session=session
            )
            return result
        except Exception as e:
            return {
                "is_complete": False,
                "missing_materials": ["API调用失败"],
                "invalid_materials": [],
                "needs_manual_review": True,
                "manual_review_reason": f"API调用失败: {str(e)}",
                "reason": "API调用失败"
            }
    
    async def _ai_judge_accident_async(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str,
        session: 'aiohttp.ClientSession'
    ) -> Dict:
        """异步版本: 事故判责"""
        ocr_summary = self._summarize_ocr_results(ocr_results)
        
        coverage_section = self._extract_section(
            policy_terms, '一、 权益内容', '二、 不属于权益范围'
        )
        exclusions_section = self._extract_section(
            policy_terms, '二、 不属于权益范围', '三、 权益人义务'
        )
        definitions_section = self._extract_section(
            policy_terms, '九、 释义', '十、 服务区域'
        )
        
        prompt = self.prompt_loader.format(
            '03_accident_judgment',
            namespace=self.prompt_namespace,
            accident_description=claim_info.get('Description_of_Accident') or '未提供事故描述',
            accident_date=claim_info.get('Date_of_Accident') or '未知',
            insured_name=claim_info.get('Insured_And_Policy') or '未知',
            ocr_summary=json.dumps(ocr_summary, ensure_ascii=False, indent=2),
            policy_terms_coverage=coverage_section,
            policy_terms_exclusions=exclusions_section,
            policy_terms_definitions=definitions_section
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = await self.client.chat_completion_json_async(
                messages=messages,
                difficulty=TaskDifficulty.HARD,
                session=session
            )
            return result
        except Exception as e:
            return {
                "accident_type": "未知",
                "is_covered": False,
                "coverage_reason": "API调用失败",
                "is_excluded": False,
                "exclusion_reason": "",
                "final_judgment": "需要人工审核",
                "reason": f"API调用失败: {str(e)}"
            }
    
    async def _ai_calculate_compensation_async(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str,
        coverage_result: Dict,
        session: 'aiohttp.ClientSession'
    ) -> Dict:
        """异步版本: 赔偿金额核算"""
        ocr_summary = self._summarize_ocr_results(ocr_results)

        # 金额硬抽取已拆到模块 extractors（避免 claim_ai_reviewer.py 越来越大）
        
        prompt = self.prompt_loader.format(
            '04_compensation_calculation',
            namespace=self.prompt_namespace,
            claim_amount=claim_info.get('Amount', ''),
            accident_date=claim_info.get('Date_of_Accident', ''),
            remaining_coverage=coverage_result.get('remaining_amount', 0),
            ocr_summary=json.dumps(ocr_summary, ensure_ascii=False, indent=2),
            depreciation_rate=config.DEPRECIATION_RATE,
            single_item_limit=config.SINGLE_ITEM_LIMIT
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = await self.client.chat_completion_json_async(
                messages=messages,
                difficulty=TaskDifficulty.MEDIUM,
                session=session
            )
            # 后处理：从材料硬抽取关键金额，并重算关键金额，保证与人工口径一致
            try:
                # 1) 优先用购买凭证硬抽取“原价/购买日”
                purchase_info = extract_purchase_amount_and_date(
                    ocr_results,
                    remaining_amount=float(coverage_result.get("remaining_amount", 0) or 0),
                    insured_amount=float(claim_info.get("Insured_Amount", 0) or 0),
                    single_item_limit=float(getattr(config, "SINGLE_ITEM_LIMIT", 1000) or 1000),
                )
                result.setdefault("extraction_debug", {})
                result["extraction_debug"]["purchase"] = purchase_info
                if purchase_info.get("amount") is not None:
                    result["original_value"] = float(purchase_info["amount"])  # type: ignore[arg-type]
                else:
                    # 金额极其关键：宁缺毋滥。未硬抽取到“实付/实付款”则不采信模型原价，避免回落到保额/限额(如5000)。
                    result["original_value"] = 0.0
                if purchase_info.get("purchase_date"):
                    result["purchase_date"] = str(purchase_info["purchase_date"])

                tp_info = extract_third_party_compensation_amount(ocr_results)
                result["extraction_debug"]["third_party_compensation"] = tp_info
                extracted_tp = tp_info.get("amount")
                if extracted_tp is not None:
                    result["third_party_compensation"] = float(extracted_tp)

                # 2) 基于“硬抽取后的原价/第三方赔付”和模型给出的折旧月数等，进行确定性重算
                original_value = float(result.get("original_value") or 0)
                dep_months = float(result.get("depreciation_months") or 0)
                dep_rate = float(result.get("depreciation_rate") or config.DEPRECIATION_RATE)

                tp_paid = float(result.get("third_party_compensation") or 0)
                acv = max(0.0, original_value * (1.0 - dep_rate * dep_months)) if original_value > 0 else 0.0
                after_tp = max(0.0, acv - tp_paid)

                single_limit = float(result.get("single_item_limit") or config.SINGLE_ITEM_LIMIT)
                after_item = min(after_tp, single_limit)
                remaining = float(result.get("remaining_coverage") or coverage_result.get("remaining_amount", 0) or 0)

                result["actual_cash_value"] = round(acv, 2)
                result["after_third_party"] = round(after_tp, 2)
                result["single_item_limit"] = single_limit
                result["after_item_limit"] = round(after_item, 2)
                result["remaining_coverage"] = remaining
                result["final_amount"] = round(max(0.0, min(after_item, remaining)), 2)

                # 3) 用“最终字段”重建 calculation_steps，避免模型旧推理残留（如把5000/500误当原价）
                try:
                    purchase_src = ""
                    try:
                        purchase_src = str((purchase_info or {}).get("matched_by") or "")
                    except Exception:
                        purchase_src = ""
                    steps = []
                    steps.append(
                        f"1) 原价(实付)={original_value:.2f}"
                        + (f"（来源:{purchase_src}）" if purchase_src else "")
                        + (f"，购买日期={result.get('purchase_date')}" if result.get("purchase_date") else "")
                        + "。"
                    )
                    steps.append(
                        f"2) 折旧月数={dep_months:.0f}，折旧率={dep_rate:.4f}。"
                    )
                    steps.append(
                        f"3) 实际现金价值=原价×(1-折旧率×月数)={original_value:.2f}×(1-{dep_rate:.4f}×{dep_months:.0f})={result['actual_cash_value']:.2f}。"
                    )
                    steps.append(
                        f"4) 第三方已赔付={tp_paid:.2f}，扣减后={result['after_third_party']:.2f}。"
                    )
                    steps.append(
                        f"5) 单件限额={single_limit:.2f}，限额后={result['after_item_limit']:.2f}。"
                    )
                    steps.append(
                        f"6) 剩余保额={remaining:.2f}，最终赔付={result['final_amount']:.2f}。"
                    )
                    result["calculation_steps"] = "".join(steps)
                except Exception:
                    pass

                if purchase_info.get("amount") is None:
                    result["reason"] = "未在购买凭证中识别到“实付/实付款”金额，无法可靠确定原价，请人工核对购买凭证金额。"
                else:
                    result["reason"] = "已从购买凭证识别到实付金额作为原价，并按折旧/第三方赔付/限额/保额规则重算。"
            except Exception:
                pass

            return result
        except Exception as e:
            claim_amount = float(claim_info.get('Amount', 0))
            return {
                "original_value": claim_amount,
                "purchase_date": "未知",
                "depreciation_months": 0,
                "depreciation_rate": config.DEPRECIATION_RATE,
                "actual_cash_value": claim_amount,
                "third_party_compensation": 0,
                "after_third_party": claim_amount,
                "single_item_limit": config.SINGLE_ITEM_LIMIT,
                "after_item_limit": min(claim_amount, config.SINGLE_ITEM_LIMIT),
                "remaining_coverage": coverage_result.get('remaining_amount', 0),
                "final_amount": 0,
                "calculation_steps": "API调用失败",
                "reason": f"API调用失败: {str(e)}"
            }
    
    def _extract_section(self, text: str, start_marker: str, end_marker: str) -> str:
        """从文本中提取指定章节"""
        return extract_section(text, start_marker, end_marker)

        try:
            start_idx = text.find(start_marker)
            end_idx = text.find(end_marker)
            if start_idx != -1 and end_idx != -1:
                return text[start_idx:end_idx]
            return ""
        except:
            return ""


def main():
    """测试AI审核系统"""
    # TODO: 从环境变量读取API密钥
    api_key = os.getenv('OPENAI_API_KEY', 'your-api-key')
    
    reviewer = AIClaimReviewer(api_key=api_key)
    
    # 读取保险条款
    from app.policy_terms_registry import POLICY_TERMS
    policy_terms_file = POLICY_TERMS.resolve("baggage_damage")
    with open(policy_terms_file, 'r', encoding='utf-8') as f:
        policy_terms = f.read()
    
    # 审核所有案件
    claims_dir = Path('claims_data')
    claim_folders = [p.parent for p in claims_dir.rglob("claim_info.json")]
    
    LOGGER.info(f"找到 {len(claim_folders)} 个案件待审核", extra=_log_extra(stage="runner"))
    
    for claim_folder in claim_folders[:1]:  # 先测试一个
        LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
        LOGGER.info(f"审核案件: {claim_folder.name}", extra=_log_extra(stage="runner"))
        LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
        
        result = reviewer.review_claim_with_ai(claim_folder, policy_terms)
        
        LOGGER.info("审核结果:", extra=_log_extra(stage="runner"))
        LOGGER.info(json.dumps(result, ensure_ascii=False, indent=2), extra=_log_extra(stage="runner"))
        
        # 保存结果
        claim_type = str(result.get("claim_type") or result.get("claimType") or "baggage_damage")
        output_dir = config.REVIEW_RESULTS_DIR / claim_type
        output_dir.mkdir(parents=True, exist_ok=True)
        
        fid = str(result.get("forceid") or claim_folder.name)
        result_file = output_dir / f"{fid}_ai_review.json"
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        LOGGER.info(f"结果已保存: {result_file}", extra=_log_extra(stage="runner"))

    
    def _extract_section(self, text: str, start_marker: str, end_marker: str) -> str:
        """从文本中提取指定章节"""
        try:
            start_idx = text.find(start_marker)
            end_idx = text.find(end_marker)
            if start_idx != -1 and end_idx != -1:
                return text[start_idx:end_idx]
            return ""
        except:
            return ""
    
    def _fallback_check_coverage(self, claim_info: Dict) -> Dict:
        """降级方案: 使用规则引擎检查保障责任"""
        from datetime import datetime
        
        benefit_name = claim_info.get('BenefitName', '')
        has_coverage = benefit_name == '随身财产'
        
        # 检查日期
        try:
            accident_date = datetime.strptime(claim_info.get('Date_of_Accident', ''), '%Y-%m-%d')
            effective_date = datetime.strptime(claim_info.get('Effective_Date', ''), '%Y%m%d%H%M%S')
            expiry_date = datetime.strptime(claim_info.get('Expiry_Date', ''), '%Y%m%d%H%M%S')
            in_coverage_period = effective_date <= accident_date <= expiry_date
        except:
            in_coverage_period = False
        
        claim_amount = float(claim_info.get('Amount', 0))
        remaining_coverage = float(claim_info.get('Remaining_Coverage', 0))
        exceeds_limit = claim_amount > remaining_coverage
        
        return {
            "has_coverage": has_coverage,
            "in_coverage_period": in_coverage_period,
            "exceeds_limit": exceeds_limit,
            "reason": "使用规则引擎降级处理",
            "single_limit": float(claim_info.get('Insured_Amount', 0)),
            "remaining_amount": remaining_coverage
        }
    
    def _fallback_check_materials(self, claim_info: Dict, ocr_results: Dict) -> Dict:
        """降级方案: 简单的材料检查"""
        return {
            "is_complete": False,
            "missing_materials": ["需要人工审核"],
            "invalid_materials": [],
            "needs_manual_review": True,
            "manual_review_reason": "AI审核失败,转人工处理",
            "reason": "使用规则引擎降级处理"
        }

    
    def _ai_judge_accident(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str
    ) -> Dict:
        """
        子Prompt 3: 事故判责
        难度: HARD (需要理解复杂的条款和判断逻辑)
        """
        ocr_summary = self._summarize_ocr_results(ocr_results)
        
        # 提取条款的各个部分
        coverage_section = self._extract_section(
            policy_terms, '一、 权益内容', '二、 不属于权益范围'
        )
        exclusions_section = self._extract_section(
            policy_terms, '二、 不属于权益范围', '三、 权益人义务'
        )
        definitions_section = self._extract_section(
            policy_terms, '九、 释义', '十、 服务区域'
        )
        
        # 使用prompt加载器
        prompt = self.prompt_loader.format(
            '03_accident_judgment',
            accident_description=claim_info.get('Description_of_Accident') or '未提供事故描述',
            accident_date=claim_info.get('Date_of_Accident') or '未知',
            insured_name=claim_info.get('Insured_And_Policy') or '未知',
            ocr_summary=json.dumps(ocr_summary, ensure_ascii=False, indent=2),
            policy_terms_coverage=coverage_section,
            policy_terms_exclusions=exclusions_section,
            policy_terms_definitions=definitions_section
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = self.client.chat_completion_json(
                messages=messages,
                difficulty=TaskDifficulty.HARD
            )
            LOGGER.info(
                f"模型返回: {result.get('reason', '')}",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="accident"),
            )
            return result
        except Exception as e:
            LOGGER.warning(
                f"API调用失败: {e}",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="accident"),
            )
            return {
                "accident_type": "未知",
                "is_covered": False,
                "coverage_reason": "API调用失败",
                "is_excluded": False,
                "exclusion_reason": "",
                "final_judgment": "需要人工审核",
                "reason": "使用规则引擎降级处理"
            }
    
    def _ai_calculate_compensation(
        self,
        claim_info: Dict,
        ocr_results: Dict,
        policy_terms: str,
        coverage_result: Dict
    ) -> Dict:
        """
        子Prompt 4: 赔偿金额核算
        难度: MEDIUM (数学计算和规则应用)
        """
        ocr_summary = self._summarize_ocr_results(ocr_results)
        
        # 使用prompt加载器
        prompt = self.prompt_loader.format(
            '04_compensation_calculation',
            claim_amount=claim_info.get('Amount', ''),
            accident_date=claim_info.get('Date_of_Accident', ''),
            remaining_coverage=coverage_result.get('remaining_amount', 0),
            ocr_summary=json.dumps(ocr_summary, ensure_ascii=False, indent=2),
            depreciation_rate=config.DEPRECIATION_RATE,
            single_item_limit=config.SINGLE_ITEM_LIMIT
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = self.client.chat_completion_json(
                messages=messages,
                difficulty=TaskDifficulty.MEDIUM
            )
            LOGGER.info(
                f"模型返回: 最终赔付 {result.get('final_amount', 0)} 元",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="compensation"),
            )
            return result
        except Exception as e:
            LOGGER.warning(
                f"API调用失败: {e}",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="compensation"),
            )
            claim_amount = float(claim_info.get('Amount', 0))
            return {
                "original_value": claim_amount,
                "purchase_date": "未知",
                "depreciation_months": 0,
                "depreciation_rate": config.DEPRECIATION_RATE,
                "actual_cash_value": claim_amount,
                "third_party_compensation": 0,
                "after_third_party": claim_amount,
                "single_item_limit": config.SINGLE_ITEM_LIMIT,
                "after_item_limit": min(claim_amount, config.SINGLE_ITEM_LIMIT),
                "remaining_coverage": coverage_result.get('remaining_amount', 0),
                "final_amount": 0,
                "calculation_steps": "API调用失败,需要人工核算",
                "reason": "使用规则引擎降级处理"
            }
    
    def _ai_final_summary(
        self,
        claim_info: Dict,
        coverage_result: Dict,
        material_result: Dict,
        accident_result: Dict,
        compensation_result: Dict
    ) -> Dict:
        """
        子Prompt 5: 最终汇总
        难度: MEDIUM (整合各阶段结果)
        """
        # 使用prompt加载器
        prompt = self.prompt_loader.format(
            '05_final_summary',
            namespace=self.prompt_namespace,
            forceid=claim_info.get('forceid', 'unknown'),
            policy_no=claim_info.get('PolicyNo', ''),
            insured_name=claim_info.get('Insured_And_Policy', ''),
            coverage_result=json.dumps(coverage_result, ensure_ascii=False, indent=2),
            material_result=json.dumps(material_result, ensure_ascii=False, indent=2),
            accident_result=json.dumps(accident_result, ensure_ascii=False, indent=2),
            compensation_result=json.dumps(compensation_result, ensure_ascii=False, indent=2)
        )
        
        messages = [{"role": "user", "content": prompt}]
        
        try:
            result = self.client.chat_completion_json(
                messages=messages,
                difficulty=TaskDifficulty.MEDIUM
            )
            return result
        except Exception as e:
            LOGGER.warning(
                f"API调用失败: {e}",
                extra=_log_extra(forceid=str(claim_info.get('forceid', '-') or '-'), stage="final"),
            )
            # 降级: 手动生成汇总结果
            return self._fallback_final_summary(
                claim_info, coverage_result, material_result, 
                accident_result, compensation_result
            )
    
    def _fallback_final_summary(
        self,
        claim_info: Dict,
        coverage_result: Dict,
        material_result: Dict,
        accident_result: Dict,
        compensation_result: Dict
    ) -> Dict:
        """降级方案: 手动生成最终汇总"""
        forceid = claim_info.get('forceid', 'unknown')
        
        # 判断各个核对点
        key_conclusions = []
        
        # 1. 保障责任核对
        coverage_eligible = coverage_result.get('has_coverage', False) and \
                          coverage_result.get('in_coverage_period', False) and \
                          not coverage_result.get('exceeds_limit', True)
        key_conclusions.append({
            "checkpoint": "保障责任核对",
            "Eligible": "Y" if coverage_eligible else "N",
            "Remark": coverage_result.get('reason', '')
        })
        
        # 2. 材料完整性核对
        material_eligible = material_result.get('is_complete', False)
        key_conclusions.append({
            "checkpoint": "材料完整性核对",
            "Eligible": "Y" if material_eligible else "N",
            "Remark": material_result.get('reason', '')
        })
        
        # 3. 保障范围核对
        coverage_scope_eligible = accident_result.get('is_covered', False)
        key_conclusions.append({
            "checkpoint": "保障范围核对",
            "Eligible": "Y" if coverage_scope_eligible else "N",
            "Remark": accident_result.get('coverage_reason', '')
        })
        
        # 4. 除外责任核对
        exclusion_eligible = not accident_result.get('is_excluded', True)
        key_conclusions.append({
            "checkpoint": "除外责任核对",
            "Eligible": "Y" if exclusion_eligible else "N",
            "Remark": accident_result.get('exclusion_reason', '未触发除外责任')
        })
        
        # 5. 赔偿金额核对
        compensation_eligible = compensation_result.get('final_amount', 0) > 0
        key_conclusions.append({
            "checkpoint": "赔偿金额核对",
            "Eligible": "Y" if compensation_eligible else "N",
            "Remark": compensation_result.get('calculation_steps', '')
        })
        
        # 判断是否需要补件
        needs_additional = material_result.get('needs_manual_review', False) or \
                          not material_result.get('is_complete', False)
        
        # 生成最终结论
        if needs_additional:
            missing = material_result.get('missing_materials', [])
            remark = f"需要补充材料: {', '.join(missing)}" if missing else "需要人工审核"
        elif not coverage_eligible:
            remark = "拒赔: 不符合保障责任要求"
        elif not coverage_scope_eligible:
            remark = "拒赔: 不属于保障范围"
        elif not exclusion_eligible:
            remark = f"拒赔: {accident_result.get('exclusion_reason', '触发除外责任')}"
        elif compensation_eligible:
            final_amount = compensation_result.get('final_amount', 0)
            remark = f"审核通过,同意赔付{final_amount}元"
        else:
            remark = "拒赔: 赔偿金额为0"
        
        return {
            "forceid": forceid,
            "Remark": remark,
            "IsAdditional": "Y" if needs_additional else "N",
            "KeyConclusions": key_conclusions
        }



def main():
    """同步审核系统(兼容旧版)"""
    import asyncio
    asyncio.run(main_async())


async def main_async():
    """异步并发审核系统 - 大幅提升处理速度"""
    import aiohttp
    import time
    import re
    
    # 验证配置
    if not config.validate():
        LOGGER.error("配置验证失败,请检查.env文件", extra=_log_extra(stage="runner"))
        return
    
    reviewer = AIClaimReviewer()
    
    # 读取保险条款（当前强制随身财产）
    policy_terms_file = POLICY_TERMS.resolve("baggage_damage")
    with open(policy_terms_file, 'r', encoding='utf-8') as f:
        policy_terms = f.read()
    
    # 审核案件目录枚举：
    # - 兼容平铺：claims_data/<case_folder>/claim_info.json
    # - 兼容命名空间：claims_data/<claim_type>/<case_folder>/claim_info.json
    claims_dir = config.CLAIMS_DATA_DIR
    claim_folders = []
    for info_file in claims_dir.rglob("claim_info.json"):
        folder = info_file.parent
        if folder.name.startswith("."):
            continue
        claim_folders.append(folder)
    # 去重&排序（避免重复处理/保证日志稳定）
    claim_folders = sorted(set(claim_folders), key=lambda p: str(p))

    # 可选：只跑某个案件类型（避免 main.py 全量跑航延+随身财产）
    # 支持：ONLY_CLAIM_TYPE=flight_delay / baggage_damage
    only_claim_type = (os.getenv("ONLY_CLAIM_TYPE") or "").strip()
    if only_claim_type:
        only_claim_type = only_claim_type.lower()
        if only_claim_type not in {"flight_delay", "baggage_damage"}:
            LOGGER.warning(
                f"ONLY_CLAIM_TYPE={only_claim_type} 不支持，将忽略（仅支持 flight_delay/baggage_damage）",
                extra=_log_extra(stage="runner"),
            )
        else:
            filtered = []
            for folder in claim_folders:
                try:
                    with open(folder / "claim_info.json", "r", encoding="utf-8") as f:
                        info = json.load(f)
                    benefit = str(info.get("BenefitName") or "")
                except Exception:
                    benefit = ""
                is_fd = ("航班延误" in benefit) or ("航班延误" in str(folder))
                if only_claim_type == "flight_delay" and is_fd:
                    filtered.append(folder)
                elif only_claim_type == "baggage_damage" and (not is_fd):
                    filtered.append(folder)
            claim_folders = filtered

    # 可选：只跑指定 forceid（支持单个/逗号列表/区间 start~end）
    # 用法：
    # - ONLY_FORCEID=a0n...
    # - ONLY_FORCEID=a0n1,a0n2
    # - ONLY_FORCEID=a0nStart~a0nEnd
    only_forceid = (os.getenv("ONLY_FORCEID") or "").strip()
    if only_forceid:
        # 先建立 forceid->folder 映射
        mapping = {}
        for folder in claim_folders:
            try:
                with open(folder / "claim_info.json", "r", encoding="utf-8") as f:
                    info = json.load(f)
                fid = str(info.get("forceid") or "").strip()
                if fid:
                    mapping[fid] = folder
            except Exception:
                continue

        selected_folders = []
        if "~" in only_forceid:
            start, end = [x.strip() for x in only_forceid.split("~", 1)]
            keys = sorted(mapping.keys())
            for k in keys:
                if start <= k <= end:
                    selected_folders.append(mapping[k])
        elif "," in only_forceid or "，" in only_forceid:
            parts = [p.strip() for p in re.split(r"[，,]", only_forceid) if p.strip()]
            for fid in parts:
                folder = mapping.get(fid)
                if folder and folder not in selected_folders:
                    selected_folders.append(folder)
        else:
            folder = mapping.get(only_forceid)
            if folder:
                selected_folders = [folder]

        if not selected_folders:
            LOGGER.warning(
                f"ONLY_FORCEID={only_forceid} 未匹配到案件，将退出",
                extra=_log_extra(stage="runner"),
            )
            return
        claim_folders = selected_folders
    
    total_claims = len(claim_folders)
    LOGGER.info(f"找到 {total_claims} 个案件待审核", extra=_log_extra(stage="runner"))
    LOGGER.info("使用异步并发处理 (每批最多10个案件同时处理)", extra=_log_extra(stage="runner"))
    
    start_time = time.time()
    
    # 创建共享的aiohttp session（使用系统代理）
    http_proxy = os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
    https_proxy = os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
    
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(
        connector=connector,
        trust_env=True  # 使用环境变量中的代理设置
    ) as session:
        # 主流程重试配置（仅针对真正抛出的异常，如网络错误/超时等）
        main_max_retries = int(os.getenv("MAIN_MAX_RETRIES", "3"))
        main_retry_sleep = float(os.getenv("MAIN_RETRY_SLEEP_SEC", "3"))

        async def _run_with_retry(claim_folder: Path, idx: int) -> Optional[Dict]:
            last_exc: Exception | None = None
            for attempt in range(1, max(1, main_max_retries) + 1):
                try:
                    return await review_claim_async(
                        reviewer, claim_folder, policy_terms,
                        idx, total_claims, session
                    )
                except Exception as e:  # 网络/调用层异常才会进这里
                    last_exc = e
                    if attempt < main_max_retries:
                        err_lower = str(e).lower()
                        is_conn = any(kw in err_lower for kw in ("cannot connect", "connection", "ssl", "timeout", "network"))
                        wait = main_retry_sleep * (2 ** (attempt - 1)) * (3 if is_conn else 1)
                        wait = min(wait, 120.0)
                        LOGGER.warning(
                            f"[{idx}/{total_claims}] 调用异常（{'连接错误' if is_conn else '一般错误'}），"
                            f"第 {attempt} 次失败，{wait:.1f}s 后重试...",
                            extra=_log_extra(stage="runner", attempt=attempt),
                        )
                        await asyncio.sleep(wait)
            # 重试仍失败，向上抛给批量统计
            if last_exc:
                raise last_exc
            return None
        # 分批处理，避免并发过多
        batch_size = 3  # 每批处理3个案件(降低并发数)
        all_results = []
        
        for batch_start in range(0, total_claims, batch_size):
            batch_end = min(batch_start + batch_size, total_claims)
            batch_folders = claim_folders[batch_start:batch_end]
            
            LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
            LOGGER.info(f"处理批次: {batch_start+1}-{batch_end}/{total_claims}", extra=_log_extra(stage="runner"))
            LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
            
            # 创建当前批次的任务
            tasks = []
            for i, claim_folder in enumerate(batch_folders, batch_start + 1):
                task = _run_with_retry(claim_folder, i)
                tasks.append(task)
            
            # 并发执行当前批次
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results.extend(batch_results)
        
        results = all_results
    
    # 处理结果
    all_results = []
    success_count = 0
    failed_count = 0
    
    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            failed_count += 1
            LOGGER.error(f"[失败] 案件 {i} 审核失败: {result}", extra=_log_extra(stage="runner"))
        elif result is not None:
            all_results.append(result)
            success_count += 1
            
            # 保存单个案件结果（按 claim_type 命名空间隔离，避免不同业务混用）
            claim_type = str(result.get("claim_type") or result.get("claimType") or "baggage_damage")
            output_dir = config.REVIEW_RESULTS_DIR / claim_type
            output_dir.mkdir(parents=True, exist_ok=True)
            result_file = output_dir / f"{result['forceid']}_ai_review.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
    
    # 生成API标准返回格式
    api_response = {
        "msg": None,
        "code": 200,
        "data": all_results
    }
    
    # 保存汇总结果
    # 汇总结果固定落到 runner 目录（可能包含多类型案件）
    summary_file = (config.REVIEW_RESULTS_DIR / "_runner") / "api_response.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(api_response, f, ensure_ascii=False, indent=2)
    
    # 计算耗时
    elapsed_time = time.time() - start_time
    
    # 打印审核统计
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info("批量审核完成!", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))
    LOGGER.info(f"总案件数: {total_claims}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"成功: {success_count}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"失败: {failed_count}", extra=_log_extra(stage="runner"))
    LOGGER.info(f"成功率: {success_count/total_claims*100:.1f}%", extra=_log_extra(stage="runner"))
    LOGGER.info(f"总耗时: {elapsed_time:.1f}秒", extra=_log_extra(stage="runner"))
    LOGGER.info(f"平均速度: {elapsed_time/total_claims:.1f}秒/案件", extra=_log_extra(stage="runner"))
    LOGGER.info(f"API响应已保存: {summary_file}", extra=_log_extra(stage="runner"))
    LOGGER.info("=" * 60, extra=_log_extra(stage="runner"))


async def review_claim_async(
    reviewer: 'AIClaimReviewer',
    claim_folder: Path,
    policy_terms: str,
    index: int,
    total: int,
    session: aiohttp.ClientSession
) -> Optional[Dict]:
    """异步审核单个案件。"""
    forceid: str = "unknown"
    ctx = {
        "coverage": None,
        "materials": None,
        "accident": None,
        "compensation": None,
        "debug": [],
    }

    stage_max_retries = int(os.getenv("STAGE_MAX_RETRIES", os.getenv("MAIN_MAX_RETRIES", "3")))
    stage_retry_sleep = float(os.getenv("STAGE_RETRY_SLEEP_SEC", os.getenv("MAIN_RETRY_SLEEP_SEC", "3")))

    try:
        claim_info = reviewer._load_claim_info(claim_folder)
        claim_info["_claim_folder_path"] = str(claim_folder)
        forceid = claim_info.get('forceid', 'unknown')

        benefit = str(claim_info.get("BenefitName") or "")
        claim_type = "flight_delay" if ("航班延误" in benefit or "航班延误" in str(claim_folder)) else "baggage_damage"
        reviewer.set_claim_type(claim_type)
        claim_info["claim_type"] = claim_type

        LOGGER.info(
            f"[{index}/{total}] 开始审核 {claim_folder.name}",
            extra=_log_extra(forceid=forceid, stage="start", attempt=0),
        )

        if claim_type == "flight_delay":
            return await review_flight_delay_async(
                reviewer=reviewer,
                claim_folder=claim_folder,
                claim_info=claim_info,
                policy_terms=policy_terms or "",
                index=index,
                total=total,
                session=session,
            )

        return await review_baggage_damage_async(
            reviewer=reviewer,
            claim_folder=claim_folder,
            claim_info=claim_info,
            policy_terms=policy_terms or "",
            index=index,
            total=total,
            session=session,
            stage_max_retries=stage_max_retries,
            stage_retry_sleep=stage_retry_sleep,
            ctx=ctx,
        )

    except Exception as e:
        LOGGER.error(
            f"[{index}/{total}] 异常: {e}",
            extra=_log_extra(forceid=forceid, stage="exception", attempt=0),
        )
        import traceback
        traceback.print_exc()

        return {
            "forceid": forceid,
            "claim_type": claim_info.get("claim_type", "baggage_damage") if "claim_info" in locals() else "baggage_damage",
            "Remark": f"系统异常，转人工处理: {str(e)}",
            "IsAdditional": "Y",
            "KeyConclusions": [
                {
                    "checkpoint": "系统异常",
                    "Eligible": "需人工判断",
                    "Remark": str(e),
                }
            ],
            "DebugInfo": ctx,
        }

if __name__ == "__main__":
    main()


