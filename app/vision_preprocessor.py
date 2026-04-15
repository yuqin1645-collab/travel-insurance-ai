#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将案件材料预处理为"可控体积"的多模态输入:
- 图片: 缩放 + 转JPEG
- PDF: 抽取前N页渲染为JPEG

目的: 避免 base64 直接塞大PDF/大量OCR文本导致 400 / 超上下文。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from app.config import config


@dataclass(frozen=True)
class VisionAttachment:
    """发送给多模态模型的单个附件(本地文件路径)."""

    path: Path
    source_file: Path
    note: str


def _extract_dynamic_keywords(claim_info: Dict) -> List[str]:
    """
    从 claim_info 中动态提取关键词，用于 Vision 附件优先排序。
    优先级：航班号（数字+字母）> 出发/到达机场码 > 事故描述关键词。
    """
    keywords: List[str] = []

    # 航班号：提取 claim_info 和 description 中的航班号片段
    for field in [
        claim_info.get("Description_of_Accident", ""),
        claim_info.get("BenefitName", ""),
        claim_info.get("claimId", ""),
    ]:
        if field:
            # 提取所有字母+数字混合的航班号片段（忽略纯数字）
            import re
            for m in re.finditer(r"[A-Za-z]{2,}\d+", str(field)):
                kw = m.group().upper()
                # 添加完整航班号和前4位（前4位已有区分度）
                keywords.append(kw)
                if len(kw) >= 4:
                    keywords.append(kw[:4])
            # 也提取数字为主但有字母前缀的
            for m in re.finditer(r"[A-Za-z]+\d+", str(field)):
                kw = m.group().upper()
                if kw not in keywords:
                    keywords.append(kw)

    # 机场码（出发/到达）：从 Description 或 claim_info 其他字段中提取三字码
    import re
    for m in re.finditer(r"\b[A-Z]{3}\b", str(claim_info.get("Description_of_Accident", ""))):
        keywords.append(m.group())

    # 事故描述关键词
    desc = str(claim_info.get("Description_of_Accident", "")).lower()
    desc_keywords = [
        "延误", "取消", "改签", "delay", "cancel", "延误险",
        "航班", "飞常准", "通知", "变动",
    ]
    for kw in desc_keywords:
        if kw in desc:
            keywords.append(kw)

    return list(set(keywords))


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _to_jpeg_resized(src: Path, dst: Path) -> None:
    from PIL import Image

    _ensure_dir(dst.parent)
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        max_edge = int(config.VISION_IMAGE_MAX_EDGE)
        if max(w, h) > max_edge and max_edge > 0:
            scale = max_edge / float(max(w, h))
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            im = im.resize(new_size, Image.LANCZOS)
        im.save(dst, format="JPEG", quality=int(config.VISION_IMAGE_JPEG_QUALITY), optimize=True)


def _pdf_pages_to_jpegs(pdf_path: Path, out_dir: Path, max_pages: int) -> List[Path]:
    import fitz  # PyMuPDF

    _ensure_dir(out_dir)
    doc = fitz.open(pdf_path)
    try:
        n = min(len(doc), max(0, int(max_pages)))
        out_paths: List[Path] = []
        for i in range(n):
            page = doc.load_page(i)
            # 适度放大以保证可读性，同时控制体积
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out_path = out_dir / f"{pdf_path.stem}_p{i+1}.jpg"
            pix.save(str(out_path))
            # 再次做JPEG压缩/缩放，进一步控体积
            compressed_path = out_dir / f"{pdf_path.stem}_p{i+1}_q.jpg"
            _to_jpeg_resized(out_path, compressed_path)
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
            out_paths.append(compressed_path)
        return out_paths
    finally:
        doc.close()


def prepare_attachments_for_claim(
    claim_folder: Path,
    out_root: Path | None = None,
    claim_info: Dict | None = None,
) -> Tuple[List[VisionAttachment], Dict]:
    """
    将案件目录下的材料文件预处理为附件列表。

    参数
    ----
    claim_folder : 案件目录路径
    out_root    : 预处理图片缓存目录（默认 .cache/vision_attachments）
    claim_info  : 案件基本信息（用于动态关键词排序），可传 None（使用默认关键词）

    Returns:
      attachments: 可直接发送给 GeminiVisionClient 的本地图片路径列表
      manifest:    用于写入prompt的索引信息
    """
    out_root = out_root or (Path(".cache") / "vision_attachments")
    force_id = claim_folder.name
    out_dir = out_root / force_id
    _ensure_dir(out_dir)

    material_files = [
        f
        for f in claim_folder.iterdir()
        if f.is_file()
        and f.name != "claim_info.json"
        and f.suffix.lower() in [".jpg", ".jpeg", ".png", ".pdf"]
    ]

    attachments: List[VisionAttachment] = []
    manifest_items: List[Dict] = []

    for f in sorted(material_files, key=lambda p: p.name):
        suf = f.suffix.lower()
        if suf in [".jpg", ".jpeg", ".png"]:
            dst = out_dir / f"{f.stem}_q.jpg"
            try:
                _to_jpeg_resized(f, dst)
                attachments.append(VisionAttachment(path=dst, source_file=f, note="image"))
                manifest_items.append(
                    {"source": f.name, "attachments": [dst.name], "type": "image"}
                )
            except Exception as e:
                manifest_items.append({"source": f.name, "attachments": [], "type": "image", "error": str(e)})
        elif suf == ".pdf":
            pages_dir = out_dir / f"{f.stem}__pdf_pages"
            try:
                page_imgs = _pdf_pages_to_jpegs(f, pages_dir, config.VISION_PDF_MAX_PAGES)
                for pimg in page_imgs:
                    attachments.append(VisionAttachment(path=pimg, source_file=f, note="pdf_page"))
                manifest_items.append(
                    {"source": f.name, "attachments": [p.name for p in page_imgs], "type": "pdf"}
                )
            except Exception as e:
                manifest_items.append({"source": f.name, "attachments": [], "type": "pdf", "error": str(e)})

    # 如果附件过多，做一次"限额+优先级"筛选，确保关键材料更易被模型关注
    max_n = int(getattr(config, "VISION_MAX_ATTACHMENTS", 10) or 10)
    if max_n > 0 and len(attachments) > max_n:
        # 固定基础关键词
        _BASE_KEYWORDS = (
            "delay", "flight", "notify", "notif", "update", "change",
            "board", "ticket", "booking", "itinera", "schedule",
            "延误", "航班", "通知", "改签", "行程", "登机", "机票",
            "variflight", "飞常准", "变动", "取消", "延误险",
        )
        # 动态关键词：从 claim_info 提取航班号、机场码、事故描述关键词
        dynamic_kws = _extract_dynamic_keywords(claim_info or {}) if claim_info else []

        def score(a: VisionAttachment) -> tuple:
            # 类型优先级：PDF页面截图 > png截图 > 其它图片
            type_rank = 2
            if a.note == "pdf_page":
                type_rank = 0
            elif a.source_file and a.source_file.suffix.lower() == ".png":
                type_rank = 1
            # 文件名关键词匹配（动态 > 基础）
            fname_lower = (a.source_file.name if a.source_file else a.path.name).lower()
            base_match = any(kw in fname_lower for kw in _BASE_KEYWORDS)
            dyn_match = any(kw.lower() in fname_lower or fname_lower in kw.lower()
                            for kw in dynamic_kws)
            # 动态匹配优先级最高，基础匹配次之，无匹配最末
            kw_rank = 0 if dyn_match else (1 if base_match else 2)
            # 文件越大通常越清晰，优先保留（倒序）
            try:
                size = a.path.stat().st_size
            except Exception:
                size = 0
            return (kw_rank, type_rank, -size, a.path.name)

        attachments = sorted(attachments, key=score)[:max_n]

        kept = {a.path.name for a in attachments}
        # 同步更新 manifest 中的附件列表（只保留被选中的）
        for item in manifest_items:
            item_atts = item.get("attachments") or []
            item["attachments"] = [n for n in item_atts if n in kept]
        manifest_items = [it for it in manifest_items if (it.get("attachments") or []) or it.get("error")]

    manifest = {
        "claim_folder": claim_folder.name,
        "total_source_files": len(material_files),
        "total_attachments": len(attachments),
        "pdf_max_pages": int(config.VISION_PDF_MAX_PAGES),
        "image_max_edge": int(config.VISION_IMAGE_MAX_EDGE),
        "jpeg_quality": int(config.VISION_IMAGE_JPEG_QUALITY),
        "max_attachments": int(getattr(config, "VISION_MAX_ATTACHMENTS", 10) or 10),
        "items": manifest_items,
    }

    return attachments, manifest

