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
import time
import asyncio
import aiohttp
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
from app.modules.registry import DEFAULT_REGISTRY
from app.policy_terms_registry import POLICY_TERMS
from app.modules.baggage_damage.stages import (
    extract_section,
    summarize_ocr_results,
)
from app.modules.baggage_damage.pipeline import review_baggage_damage_async
from app.modules.baggage_delay.pipeline import review_baggage_delay_async
from app.modules.flight_delay.pipeline import review_flight_delay_async
from app.logging_utils import LOGGER, log_extra as _log_extra


"""
日志工具已迁移到 app.logging_utils，避免循环依赖：
- claim_ai_reviewer -> ocr_service -> ocr_cache -> claim_ai_reviewer
"""


def _detect_claim_type(benefit: str, folder_hint: str = "") -> str:
    benefit = str(benefit or "")
    folder_hint = str(folder_hint or "")
    combined = f"{benefit} {folder_hint}"
    if "行李延误" in combined:
        return "baggage_delay"
    if "航班延误" in combined or "flight_delay" in combined.lower():
        return "flight_delay"
    return "baggage_damage"


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

    async def _ai_baggage_delay_parse_async(
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

    async def _ai_baggage_delay_audit_async(
        self,
        claim_info: Dict,
        parsed: Dict,
        policy_terms_excerpt: str,
        session: 'aiohttp.ClientSession',
    ) -> Dict:
        prompt = self.prompt_loader.format(
            "02_audit_decision",
            namespace=self.prompt_namespace,
            parsed_json=json.dumps(parsed, ensure_ascii=False),
            policy_terms_excerpt=policy_terms_excerpt or "",
            claim_info_json=json.dumps(claim_info, ensure_ascii=False),
        )
        return await self.client.chat_completion_json_async(
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.MEDIUM,
            session=session,
        )

    async def _ai_pir_receipt_time_extract_async(
        self,
        attachment_paths: List[Path],
        claim_info: Dict,
        session: 'aiohttp.ClientSession',
    ) -> Dict:
        """
        PIR签收时间专项提取：当主Vision扫描确认PIR存在但未能提取签收时间时，
        对材料进行二次聚焦提取，专注于PIR报告中的行李交付/签收时间字段。
        降级：若无附件或调用失败，返回空 dict。
        """
        if not attachment_paths:
            return {}
        prompt = self.prompt_loader.format(
            "00b_pir_receipt_time_extract",
            namespace=self.prompt_namespace,
            claim_info_json=json.dumps(claim_info, ensure_ascii=False, indent=2),
        )
        return await self.vision_client.review_materials_with_vision(
            material_files=attachment_paths,
            prompt=prompt,
            session=session,
        )

    # ==================== 公共小工具 ====================
    def _extract_earliest_travel_date_from_ocr(self, ocr_results: Dict) -> Optional[datetime]:
        """
        从OCR文本中粗略提取"最早出行日期"(用于判断是否出境后才投保).
        
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
            # - 允许(更像"出行日期"): 起飞/出发/Departure/Flight date/航班日期/行程
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
                # 上下文过滤：避免把签证有效期等日期当作"出行日期"
                left = max(0, m.start() - 30)
                right = min(len(text), m.end() + 30)
                ctx = text[left:right]
                ctx_lower = ctx.lower()

                has_travel_ctx = any(k.lower() in ctx_lower for k in travel_context_keywords)
                has_exclude_ctx = any(k.lower() in ctx_lower for k in exclude_context_keywords)

                # 如果缺少任何"出行相关上下文"，直接跳过（避免护照页/签证页任意日期被采纳）
                if not has_travel_ctx:
                    continue
                # 若命中排除上下文，则仅当同时包含"出入境章"语义时才保留
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

    def _extract_section(self, text: str, start_marker: str, end_marker: str) -> str:
        """从文本中提取指定章节"""
        return extract_section(text, start_marker, end_marker)


def main():
    """同步审核系统(兼容旧版)"""
    asyncio.run(main_async())


async def main_async():
    """异步并发审核系统 - 大幅提升处理速度"""

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
    # 支持：ONLY_CLAIM_TYPE=flight_delay / baggage_delay / baggage_damage
    only_claim_type = (os.getenv("ONLY_CLAIM_TYPE") or "").strip()
    if only_claim_type:
        only_claim_type = only_claim_type.lower()
        if only_claim_type not in {"flight_delay", "baggage_delay", "baggage_damage"}:
            LOGGER.warning(
                f"ONLY_CLAIM_TYPE={only_claim_type} 不支持，将忽略（仅支持 flight_delay/baggage_delay/baggage_damage）",
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
                detected = _detect_claim_type(benefit=benefit, folder_hint=str(folder))
                if only_claim_type == detected:
                    filtered.append(folder)
                elif only_claim_type == "baggage_damage" and detected == "baggage_damage":
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
            last_exc: Optional[Exception] = None
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
        claim_type = _detect_claim_type(benefit=benefit, folder_hint=str(claim_folder))
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
        if claim_type == "baggage_delay":
            return await review_baggage_delay_async(
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
            exc_info=True,
        )

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


