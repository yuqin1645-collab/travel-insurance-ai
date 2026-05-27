from __future__ import annotations

from typing import Dict


def summarize_ocr_results(ocr_results: Dict) -> Dict:
    summary = {
        "total_files": len(ocr_results),
        "documents": [],
    }

    for filename, result in ocr_results.items():
        summary["documents"].append(
            {
                "filename": filename,
                "type": result.get("key_info", {}).get("document_type", "未知"),
                "confidence": result.get("confidence", 0),
                "key_info": result.get("key_info", {}),
            }
        )

    return summary


def extract_section(text: str, start_marker: str, end_marker: str) -> str:
    try:
        start_idx = text.find(start_marker)
        end_idx = text.find(end_marker)
        if start_idx != -1 and end_idx != -1:
            return text[start_idx:end_idx]
        return ""
    except Exception:
        return ""
