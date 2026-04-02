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
) -> Tuple[List[VisionAttachment], Dict]:
    """
    将案件目录下的材料文件预处理为附件列表。

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
        # 航班延误相关的关键词，含这些词的文件名优先保留
        _DELAY_KEYWORDS = (
            "delay", "flight", "notify", "notif", "update", "change",
            "board", "ticket", "booking", "itinera", "schedule",
            "延误", "航班", "通知", "改签", "行程", "登机", "机票",
        )

        def score(a: VisionAttachment) -> tuple:
            # 用原始文件后缀判断类型（转换后的 .jpg 不代表原始类型）
            orig_suf = a.source_file.suffix.lower() if a.source_file else a.path.suffix.lower()
            # 优先级：PDF页面截图 > png截图 > 其它图片
            type_rank = 2
            if a.note == "pdf_page":
                type_rank = 0
            elif orig_suf == ".png":
                type_rank = 1
            # 文件名含延误/航班关键词的优先保留
            fname_lower = (a.source_file.name if a.source_file else a.path.name).lower()
            keyword_rank = 0 if any(kw in fname_lower for kw in _DELAY_KEYWORDS) else 1
            # 文件越大通常越清晰，优先保留（倒序）
            try:
                size = a.path.stat().st_size
            except Exception:
                size = 0
            return (keyword_rank, type_rank, -size, a.path.name)

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

