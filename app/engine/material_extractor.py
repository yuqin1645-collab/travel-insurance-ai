from __future__ import annotations

"""
通用材料提取模块 — app/engine/material_extractor.py

提供两种提取策略，可被任意案件类型的 pipeline 复用：

  ExtractionStrategy.OCR_THEN_LLM
      先对所有材料做 OCR（PDF→文本，图片→OCRService），
      再将文本拼合后送给 LLM 做结构化解析。
      优点：文本稳定，LLM 任务更简单；
      缺点：OCR 效果影响上游，速度慢。

  ExtractionStrategy.VISION_DIRECT
      把所有材料（图片/PDF）编码后直接送给视觉模型，
      让模型"看图说话"——一次 API 调用完成提取。
      优点：速度快，无 OCR 噪声；
      缺点：依赖模型多模态能力，PDF 支持因服务商而异。

  ExtractionStrategy.HYBRID（默认）
      先尝试 OCR_THEN_LLM；如果 OCR 结果为空（无材料或全部失败），
      再 fallback 到 VISION_DIRECT。
      这样在材料充分时享受文本稳定性，材料稀少时不放弃图片理解能力。

使用方法::

    extractor = MaterialExtractor(reviewer)
    result = await extractor.extract(
        claim_folder=folder,
        claim_info=info,
        strategy=ExtractionStrategy.HYBRID,
        prompt_name="00_vision_extract",   # Vision 阶段用的 prompt key
        session=session,
    )
    # result.ocr_results  — Dict[filename, ocr_dict]  (OCR 策略时有值)
    # result.vision_data  — Dict                      (Vision 策略时有值)
    # result.strategy_used — 实际使用的策略枚举

说明：
  - 本模块不含任何业务逻辑，只负责"拿到材料文本/视觉数据"并返回。
  - claim_type 相关的字段解析（schema 定义、json key 映射）由各 pipeline 自行完成。
"""

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.logging_utils import LOGGER, log_extra


# ─────────────────────────────────────────────────────────────────────────────
# 枚举
# ─────────────────────────────────────────────────────────────────────────────

class ExtractionStrategy(enum.Enum):
    OCR_THEN_LLM = "ocr_then_llm"
    VISION_DIRECT = "vision_direct"
    HYBRID = "hybrid"


# ─────────────────────────────────────────────────────────────────────────────
# 结果数据类
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """材料提取结果。两个字段可能同时存在（HYBRID 两路都跑时）。"""

    # OCR 路径的结果：{ filename: {"text": str, "key_info": {...}, "confidence": float} }
    ocr_results: Dict[str, Any] = field(default_factory=dict)

    # Vision 路径的结果：与 vision_extract prompt 输出的 JSON 结构一致
    vision_data: Dict[str, Any] = field(default_factory=dict)

    # 实际执行的策略（HYBRID 时记录最终落地的那条路）
    strategy_used: ExtractionStrategy = ExtractionStrategy.OCR_THEN_LLM

    # 可读摘要（供日志）
    summary: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 核心类
# ──────────���──────────────────────────────────────────────────────────────────

class MaterialExtractor:
    """
    通用材料提取器。

    参数
    ----
    reviewer : AIClaimReviewer
        现有 reviewer 对象，提供 _ocr_all_materials / vision_client /
        prompt_loader 等能力。保持向后兼容，避免重复创建 client。
    forceid : str
        当前案件 ID，仅用于日志。
    """

    def __init__(self, reviewer: Any, forceid: str = "unknown") -> None:
        self._reviewer = reviewer
        self._forceid = forceid

    # ── 公共入口 ──────────────────────────────────────────────────────────────

    async def extract(
        self,
        *,
        claim_folder: Path,
        claim_info: Dict[str, Any],
        strategy: ExtractionStrategy = ExtractionStrategy.HYBRID,
        prompt_name: str = "00_vision_extract",
        session: Optional[aiohttp.ClientSession] = None,
    ) -> ExtractionResult:
        """
        按指定策略提取材料。

        参数
        ----
        claim_folder : 案件目录，包含所有附件
        claim_info   : claim_info.json 内容
        strategy     : 提取策略
        prompt_name  : Vision 阶段使用的 prompt key（相对于当前 prompt_namespace）
        session      : aiohttp.ClientSession；仅 VISION_DIRECT / HYBRID 需要
        """
        if strategy == ExtractionStrategy.OCR_THEN_LLM:
            return await self._do_ocr(claim_folder)

        if strategy == ExtractionStrategy.VISION_DIRECT:
            return await self._do_vision(claim_folder, claim_info, prompt_name, session)

        # HYBRID: OCR first, vision fallback if no OCR text
        return await self._do_hybrid(claim_folder, claim_info, prompt_name, session)

    # ── OCR 路径 ──────────────────────────────────────────────────────────────

    async def _do_ocr(self, claim_folder: Path) -> ExtractionResult:
        """调用 reviewer 的 OCR 流程，返回 OCR 结果字典。"""
        LOGGER.info(
            "材料提取 [OCR] 开始",
            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
        )
        try:
            ocr_results: Dict[str, Any] = self._reviewer._ocr_all_materials(claim_folder)
        except Exception as e:
            LOGGER.warning(
                f"材料提取 [OCR] 失败: {e}",
                extra=log_extra(forceid=self._forceid, stage="material_extractor"),
            )
            ocr_results = {}

        total_chars = sum(
            len(v.get("text") or "") for v in ocr_results.values()
        )
        summary = f"OCR识别 {len(ocr_results)} 份材料，共 {total_chars} 字符"
        LOGGER.info(
            f"材料提取 [OCR] 完成: {summary}",
            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
        )
        return ExtractionResult(
            ocr_results=ocr_results,
            strategy_used=ExtractionStrategy.OCR_THEN_LLM,
            summary=summary,
        )

    # ── Vision 路径 ───────────────────────────────────────────────────────────

    async def _download_filelist_to_folder(
        self,
        claim_folder: Path,
        claim_info: Dict[str, Any],
        session: Optional[aiohttp.ClientSession],
    ) -> int:
        """
        从 claim_info["FileList"] 下载图片到 claim_folder。
        返回成功下载的文件数量。
        """
        file_list = claim_info.get("FileList") or []
        if not file_list:
            return 0

        import asyncio as _asyncio
        import os

        proxy = (
            os.getenv('HTTPS_PROXY') or os.getenv('https_proxy')
            or os.getenv('HTTP_PROXY') or os.getenv('http_proxy')
        ) or None

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        downloaded = 0
        try:
            for idx, item in enumerate(file_list):
                url = item.get("FileUrl") or item.get("fileUrl") or item.get("url") or ""
                if not url:
                    continue
                # 从 URL 推断扩展名，默认 .jpg
                ext = ".jpg"
                url_path = url.split("?")[0]
                for candidate in [".jpg", ".jpeg", ".png", ".pdf"]:
                    if url_path.lower().endswith(candidate):
                        ext = candidate
                        break
                dst = claim_folder / f"filelist_{idx:03d}{ext}"
                if dst.exists():
                    downloaded += 1
                    continue
                try:
                    async with session.get(
                        url,
                        proxy=proxy,
                        ssl=False,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            dst.write_bytes(data)
                            downloaded += 1
                        else:
                            LOGGER.warning(
                                f"材料下载失败 [{idx}] status={resp.status}",
                                extra=log_extra(forceid=self._forceid, stage="material_extractor"),
                            )
                except Exception as e:
                    LOGGER.warning(
                        f"材料下载异常 [{idx}]: {e}",
                        extra=log_extra(forceid=self._forceid, stage="material_extractor"),
                    )
        finally:
            if close_session:
                await session.close()

        LOGGER.info(
            f"FileList 下载完成: {downloaded}/{len(file_list)} 个文件",
            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
        )
        return downloaded

    async def _do_vision(
        self,
        claim_folder: Path,
        claim_info: Dict[str, Any],
        prompt_name: str,
        session: Optional[aiohttp.ClientSession],
    ) -> ExtractionResult:
        """把所有附件发给视觉模型做一次性提取。"""
        from app.vision_preprocessor import prepare_attachments_for_claim

        LOGGER.info(
            "材料提取 [Vision] 开始",
            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
        )
        attachments, _manifest = prepare_attachments_for_claim(claim_folder)
        attachment_paths: List[Path] = [a.path for a in attachments]

        # 本地无附件时，尝试从 FileList URL 下载
        if not attachment_paths:
            n = await self._download_filelist_to_folder(claim_folder, claim_info, session)
            if n > 0:
                attachments, _manifest = prepare_attachments_for_claim(claim_folder)
                attachment_paths = [a.path for a in attachments]

        if not attachment_paths:
            LOGGER.info(
                "材料提取 [Vision] 无附件，返回空结果",
                extra=log_extra(forceid=self._forceid, stage="material_extractor"),
            )
            return ExtractionResult(
                vision_data={},
                strategy_used=ExtractionStrategy.VISION_DIRECT,
                summary="无附件",
            )

        import asyncio as _asyncio
        import json as _json

        # 先用本地 Tesseract OCR 提取文本，作为辅助信息注入 Vision prompt
        # 目的：帮助视觉模型核对容易混淆的字符（如航班号末位数字 6/8）
        ocr_text_for_prompt = self._run_tesseract_on_attachments(attachment_paths)

        try:
            prompt = self._reviewer.prompt_loader.format(
                prompt_name,
                namespace=self._reviewer.prompt_namespace,
                claim_info_json=_json.dumps(claim_info, ensure_ascii=False, indent=2),
                ocr_text=ocr_text_for_prompt,
            )
        except Exception as e:
            LOGGER.warning(
                f"材料提取 [Vision] prompt构建失败: {e}",
                extra=log_extra(forceid=self._forceid, stage="material_extractor"),
            )
            vision_data = {}
            prompt = None  # type: ignore

        vision_data: Dict[str, Any] = {}
        if prompt:
            _max_attempts = 3
            for _attempt in range(1, _max_attempts + 1):
                try:
                    vision_data = await self._reviewer.vision_client.review_materials_with_vision(
                        material_files=attachment_paths,
                        prompt=prompt,
                        session=session,
                    )
                    break  # 成功，退出重试
                except Exception as e:
                    err_str = str(e).lower()
                    is_retryable = any(k in err_str for k in (
                        "ssl", "connect", "timeout", "connection", "reset",
                        "json", "balance", "brace", "parse", "decode",  # 模型输出格式错误也重试
                    ))
                    if is_retryable and _attempt < _max_attempts:
                        LOGGER.warning(
                            f"材料提取 [Vision] 连接失败(attempt {_attempt}/{_max_attempts}), 重试中: {e}",
                            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
                        )
                        await _asyncio.sleep(3 * _attempt)
                    else:
                        LOGGER.warning(
                            f"材料提取 [Vision] 失败(attempt {_attempt}/{_max_attempts}): {e}",
                            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
                        )
                        break

        summary = f"Vision识别 {len(attachment_paths)} 份附件，返回 {len(vision_data)} 个字段"
        LOGGER.info(
            f"材料提取 [Vision] 完成: {summary}",
            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
        )
        return ExtractionResult(
            vision_data=vision_data,
            strategy_used=ExtractionStrategy.VISION_DIRECT,
            summary=summary,
        )

    # ── Tesseract OCR 辅助 ────────────────────────────────────────────────────

    def _run_tesseract_on_attachments(self, attachment_paths: List[Path]) -> str:
        """
        对所有图片附件跑 Tesseract OCR，返回拼接后的文本。
        优先使用原始文件（非 _q.jpg 压缩版），以保证字符识别精度。
        仅用于辅助 Vision prompt，失败时静默降级返回空字符串。
        """
        try:
            import pytesseract
            from PIL import Image
            from app.config import config as _cfg
            tesseract_path = getattr(_cfg, "TESSERACT_PATH", None) or r"D:\app\tools\other\Tesseract\tesseract.exe"
            if not Path(tesseract_path).exists():
                return ""
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        except ImportError:
            return ""

        import re as _re

        # 从 _q.jpg 路径推断原始文件：
        # .cache/vision_attachments/<case>/filelist_005_q.jpg
        # → claims_data/**/<case>/filelist_005.png 等
        def _find_original(p: Path) -> Path:
            name = p.name
            if "_q.jpg" not in name:
                return p
            stem = name.replace("_q.jpg", "")
            stem = _re.sub(r"_p\d+$", "", stem)  # 去掉 _p1/_p2 分页后缀
            # 从 .cache 路径推断 claims_data 中的对应目录
            # 路径结构：.cache/vision_attachments/<case_folder>/filelist_XXX_q.jpg
            case_folder_name = p.parent.name
            from app.config import config as _cfg2
            claims_dir = getattr(_cfg2, "CLAIMS_DATA_DIR", None) or Path("claims_data")
            # 在 claims_data 下递归找同名案件目录
            for candidate_dir in Path(claims_dir).rglob(case_folder_name):
                if candidate_dir.is_dir():
                    for ext in (".png", ".jpg", ".jpeg"):
                        orig = candidate_dir / f"{stem}{ext}"
                        if orig.exists():
                            return orig
            return p  # 找不到则用压缩版

        texts = []
        seen_stems: set = set()
        for p in attachment_paths:
            orig = _find_original(p)
            key = orig.stem if orig != p else p.stem
            if key in seen_stems:
                continue
            seen_stems.add(key)
            if orig.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp", ".tiff"):
                continue
            try:
                img = Image.open(orig)
                text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                text = text.strip()
                if text:
                    texts.append(f"[{orig.name}]\n{text}")
            except Exception:
                continue

        if not texts:
            return ""
        joined = "\n\n".join(texts)
        return joined[:6000]

    # ── HYBRID 路径 ───────────────────────────────────────────────────────────

    async def _do_hybrid(
        self,
        claim_folder: Path,
        claim_info: Dict[str, Any],
        prompt_name: str,
        session: Optional[aiohttp.ClientSession],
    ) -> ExtractionResult:
        """
        先 OCR，若 OCR 文本有效则直接返回；否则 fallback 到 Vision。
        有效定义：至少一份材料拥有 ≥ 30 字符的文本。
        """
        ocr_res = await self._do_ocr(claim_folder)

        has_text = any(
            len((v.get("text") or "")) >= 30
            for v in ocr_res.ocr_results.values()
        )
        if has_text:
            LOGGER.info(
                "材料提取 [HYBRID] OCR 结果有效，无需 Vision fallback",
                extra=log_extra(forceid=self._forceid, stage="material_extractor"),
            )
            ocr_res.strategy_used = ExtractionStrategy.HYBRID
            return ocr_res

        LOGGER.info(
            "材料提取 [HYBRID] OCR 文本不足，启动 Vision fallback",
            extra=log_extra(forceid=self._forceid, stage="material_extractor"),
        )
        vision_res = await self._do_vision(claim_folder, claim_info, prompt_name, session)
        # 把 OCR 结果也一并保留（即便为空），供调用方参考
        vision_res.ocr_results = ocr_res.ocr_results
        vision_res.strategy_used = ExtractionStrategy.HYBRID
        return vision_res
