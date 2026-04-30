from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import Any, Dict

from app.config import config
from app.logging_utils import LOGGER, log_extra
from app.openrouter_client import TaskDifficulty
from app.modules.baggage_damage.extractors import (
    extract_purchase_amount_and_date,
    extract_third_party_compensation_amount,
)
from app.vision_preprocessor import prepare_attachments_for_claim


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


async def ai_check_coverage_async(
    reviewer: Any,
    claim_info: Dict,
    policy_terms: str,
    session: Any,
) -> Dict:
    prompt = reviewer.prompt_loader.format(
        "01_coverage_check",
        namespace=reviewer.prompt_namespace,
        policy_no=claim_info.get("PolicyNo", ""),
        product_name=claim_info.get("Product_Name", ""),
        benefit_name=claim_info.get("BenefitName", ""),
        insured_amount=claim_info.get("Insured_Amount", ""),
        remaining_coverage=claim_info.get("Remaining_Coverage", ""),
        effective_date=claim_info.get("Effective_Date", ""),
        expiry_date=claim_info.get("Expiry_Date", ""),
        accident_date=claim_info.get("Date_of_Accident", ""),
        claim_amount=claim_info.get("Amount", ""),
        policy_terms_excerpt=policy_terms[:500],
    )

    try:
        return await reviewer.client.chat_completion_json_async(
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.SIMPLE,
            session=session,
        )
    except Exception as e:
        return {
            "has_coverage": False,
            "in_coverage_period": False,
            "exceeds_limit": True,
            "reason": f"API调用失败: {str(e)}",
            "single_limit": float(claim_info.get("Insured_Amount", 0)),
            "remaining_amount": float(claim_info.get("Remaining_Coverage", 0)),
        }


async def ai_check_materials_async(
    reviewer: Any,
    claim_info: Dict,
    ocr_results: Dict,
    policy_terms: str,
    session: Any,
) -> Dict:
    try:
        claim_folder = Path(claim_info.get("_claim_folder_path", "")) if claim_info.get("_claim_folder_path") else None
        if claim_folder is None:
            forceid = claim_info.get("forceid") or ""
            claim_folder = next(
                (d for d in config.CLAIMS_DATA_DIR.rglob("*") if d.is_dir() and forceid and forceid in d.name),
                None,
            )
        if claim_folder is None:
            raise RuntimeError("无法定位案件目录(vision模式需要原始材料文件)")

        all_attachments, _manifest = prepare_attachments_for_claim(claim_folder=claim_folder, max_attachments=0)

        if not all_attachments:
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
                    "损坏物品照片（如适用）",
                ],
                "invalid_materials": [],
                "needs_manual_review": False,
                "manual_review_reason": "",
                "reason": "未提交任何可识别的材料文件（图片/PDF），无法完成材料核对",
            }

        batch_size = max(1, int(getattr(config, "VISION_MAX_ATTACHMENTS", 10) or 10))
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
            batch_index = bi // batch_size + 1
            batch_total = (len(all_attachments) + batch_size - 1) // batch_size
            batch_manifest = {
                "batch_index": batch_index,
                "batch_total": batch_total,
                "attachments": [a.path.name for a in batch],
                "source_hint": [a.source_file.name for a in batch],
            }
            prompt = reviewer.prompt_loader.format(
                "02_material_evidence_vision",
                namespace=reviewer.prompt_namespace,
                benefit_name=claim_info.get("BenefitName") or "未知",
                accident_description=claim_info.get("Description_of_Accident") or "未提供事故描述",
                accident_date=claim_info.get("Date_of_Accident") or "未知",
                batch_manifest=json.dumps(batch_manifest, ensure_ascii=False, indent=2),
            )

            # 带重试的批次调用
            batch_res: Dict[str, Any] = {}
            max_attempts = max(1, int(getattr(config, "VISION_RETRY_NETWORK_MAX_ATTEMPTS", 3) or 3))
            base_delay = float(getattr(config, "VISION_RETRY_BASE_DELAY", 2.0) or 2.0)
            max_delay = float(getattr(config, "VISION_RETRY_MAX_DELAY", 20.0) or 20.0)
            jitter_ratio = max(0.0, float(getattr(config, "VISION_RETRY_JITTER", 0.35) or 0.0))
            for attempt in range(1, max_attempts + 1):
                try:
                    batch_res = await reviewer.vision_client.review_materials_with_vision(
                        material_files=[a.path for a in batch],
                        prompt=prompt,
                        session=session,
                    )
                    break
                except Exception as e:
                    err_str = str(e).lower()
                    retryable = any(k in err_str for k in (
                        "ssl", "connect", "timeout", "connection", "reset",
                        "temporarily unavailable", "invalid control character",
                        "json", "balance", "brace", "parse", "decode",
                        "expecting", "delimiter", "unterminated", "extra data",
                    ))
                    if retryable and attempt < max_attempts:
                        backoff = min(max_delay, base_delay * (2 ** (attempt - 1)))
                        wait_seconds = backoff + random.uniform(0, backoff * jitter_ratio)
                        LOGGER.warning(
                            f"baggage_damage vision batch {batch_index}/{batch_total} attempt {attempt}/{max_attempts} 失败，{wait_seconds:.1f}s 后重试: {e}",
                            extra=log_extra(forceid=claim_info.get("forceid", ""), stage="baggage_damage_material_check"),
                        )
                        await asyncio.sleep(wait_seconds)
                    else:
                        LOGGER.warning(
                            f"baggage_damage vision batch {batch_index}/{batch_total} 最终失败(attempt {attempt}/{max_attempts}): {e}",
                            extra=log_extra(forceid=claim_info.get("forceid", ""), stage="baggage_damage_material_check"),
                        )
                        break

            if not batch_res:
                continue

            present = batch_res.get("present") or {}
            evidence = batch_res.get("evidence") or {}
            notes = (batch_res.get("notes") or "").strip()
            if notes:
                merged_notes.append(notes)

            for key in merged_present.keys():
                if bool(present.get(key)):
                    merged_present[key] = True
                ev = evidence.get(key) or []
                if isinstance(ev, list):
                    merged_evidence[key].extend([str(x) for x in ev if x])

        for key in merged_evidence.keys():
            merged_evidence[key] = sorted(set(merged_evidence[key]))

        evidence_summary = {
            "present": merged_present,
            "evidence": merged_evidence,
            "notes": merged_notes[:10],
            "total_attachments": len(all_attachments),
            "batch_size": batch_size,
        }

        final_prompt = reviewer.prompt_loader.format(
            "02_material_check_evidence",
            namespace=reviewer.prompt_namespace,
            benefit_name=claim_info.get("BenefitName") or "未知",
            accident_description=claim_info.get("Description_of_Accident") or "未提供事故描述",
            accident_date=claim_info.get("Date_of_Accident") or "未知",
            evidence_summary=json.dumps(evidence_summary, ensure_ascii=False, indent=2),
        )

        result = await reviewer.client.chat_completion_json_async(
            messages=[{"role": "user", "content": final_prompt}],
            difficulty=TaskDifficulty.MEDIUM,
            session=session,
        )

        try:
            missing = result.get("missing_materials") or []
            if isinstance(missing, list) and missing:
                has_travel_ticket = bool(merged_present.get("travel_ticket"))
                has_passport_like = bool(merged_present.get("passport") or merged_present.get("visa_entry_exit"))
                id_type_text = str(claim_info.get("ID_Type") or claim_info.get("id_type") or "").strip()
                is_id_card_policy = "身份证" in id_type_text

                new_missing = []
                for m in missing:
                    ms = str(m)
                    is_passport_related = ("出入境" in ms) or ("护照类" in ms) or ("护照" in ms) or ("签证" in ms)
                    if is_passport_related:
                        # 身份证投保：默认不补护照/签证/出入境类
                        if is_id_card_policy:
                            continue
                        # 非身份证投保：若已有出行票据或护照类任一证据，去掉该缺件
                        if has_travel_ticket or has_passport_like:
                            continue
                    new_missing.append(m)

                # 身份证投保下，唯一要求补护照的场景：
                # 仅识别到护照签章/出入境页（visa_entry_exit=true）但无护照照片页（passport=false）
                if (
                    is_id_card_policy
                    and bool(merged_present.get("visa_entry_exit"))
                    and not bool(merged_present.get("passport"))
                ):
                    tip = "被保险人护照照片页"
                    if not any("护照照片页" in str(x) for x in new_missing):
                        new_missing.append(tip)

                result["missing_materials"] = new_missing
                if not new_missing and not (result.get("invalid_materials") or []) and not bool(result.get("needs_manual_review", False)):
                    result["is_complete"] = True
        except Exception:
            pass

        try:
            missing = result.get("missing_materials") or []
            if isinstance(missing, list) and missing:
                result["missing_materials"] = [m for m in missing if "损失清单" not in str(m)]
        except Exception:
            pass

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
                result["needs_manual_review"] = False
                if not (result.get("reason") or ""):
                    result["reason"] = "缺少购买凭证/发票/收据，无法核定原价"
        except Exception:
            pass

        try:
            result.setdefault("present_flags", merged_present)
        except Exception:
            pass

        return result
    except Exception as e:
        return {
            "is_complete": False,
            "missing_materials": [],
            "invalid_materials": [],
            "needs_manual_review": True,
            "manual_review_reason": f"材料审核系统异常，需要人工审核：{str(e)[:200]}",
            "reason": "材料审核系统异常，已转人工审核",
        }


async def ai_judge_accident_async(
    reviewer: Any,
    claim_info: Dict,
    ocr_results: Dict,
    policy_terms: str,
    session: Any,
) -> Dict:
    ocr_summary = summarize_ocr_results(ocr_results)
    coverage_section = extract_section(policy_terms, "一、 权益内容", "二、 不属于权益范围")
    exclusions_section = extract_section(policy_terms, "二、 不属于权益范围", "三、 权益人义务")
    definitions_section = extract_section(policy_terms, "九、 释义", "十、 服务区域")

    prompt = reviewer.prompt_loader.format(
        "03_accident_judgment",
        namespace=reviewer.prompt_namespace,
        accident_description=claim_info.get("Description_of_Accident") or "未提供事故描述",
        accident_date=claim_info.get("Date_of_Accident") or "未知",
        insured_name=claim_info.get("Insured_And_Policy") or "未知",
        ocr_summary=json.dumps(ocr_summary, ensure_ascii=False, indent=2),
        policy_terms_coverage=coverage_section,
        policy_terms_exclusions=exclusions_section,
        policy_terms_definitions=definitions_section,
    )

    try:
        return await reviewer.client.chat_completion_json_async(
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.HARD,
            session=session,
        )
    except Exception as e:
        return {
            "accident_type": "未知",
            "is_covered": False,
            "coverage_reason": "API调用失败",
            "is_excluded": False,
            "exclusion_reason": "",
            "final_judgment": "需要人工审核",
            "reason": f"API调用失败: {str(e)}",
        }


async def ai_calculate_compensation_async(
    reviewer: Any,
    claim_info: Dict,
    ocr_results: Dict,
    policy_terms: str,
    coverage_result: Dict,
    session: Any,
) -> Dict:
    ocr_summary = summarize_ocr_results(ocr_results)
    prompt = reviewer.prompt_loader.format(
        "04_compensation_calculation",
        namespace=reviewer.prompt_namespace,
        claim_amount=claim_info.get("Amount", ""),
        accident_date=claim_info.get("Date_of_Accident", ""),
        remaining_coverage=coverage_result.get("remaining_amount", 0),
        ocr_summary=json.dumps(ocr_summary, ensure_ascii=False, indent=2),
        depreciation_rate=config.DEPRECIATION_RATE,
        single_item_limit=config.SINGLE_ITEM_LIMIT,
    )

    try:
        result = await reviewer.client.chat_completion_json_async(
            messages=[{"role": "user", "content": prompt}],
            difficulty=TaskDifficulty.MEDIUM,
            session=session,
        )

        try:
            purchase_info = extract_purchase_amount_and_date(
                ocr_results,
                remaining_amount=float(coverage_result.get("remaining_amount", 0) or 0),
                insured_amount=float(claim_info.get("Insured_Amount", 0) or 0),
                single_item_limit=float(getattr(config, "SINGLE_ITEM_LIMIT", 1000) or 1000),
            )
            result.setdefault("extraction_debug", {})
            result["extraction_debug"]["purchase"] = purchase_info
            result["original_value"] = float(purchase_info["amount"]) if purchase_info.get("amount") is not None else 0.0
            if purchase_info.get("purchase_date"):
                result["purchase_date"] = str(purchase_info["purchase_date"])

            tp_info = extract_third_party_compensation_amount(ocr_results)
            result["extraction_debug"]["third_party_compensation"] = tp_info
            if tp_info.get("amount") is not None:
                result["third_party_compensation"] = float(tp_info["amount"])

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

            try:
                purchase_src = str((purchase_info or {}).get("matched_by") or "")
                steps = []
                steps.append(
                    f"1) 原价(实付)={original_value:.2f}"
                    + (f"（来源:{purchase_src}）" if purchase_src else "")
                    + (f"，购买日期={result.get('purchase_date')}" if result.get("purchase_date") else "")
                    + "。"
                )
                steps.append(f"2) 折旧月数={dep_months:.0f}，折旧率={dep_rate:.4f}。")
                steps.append(
                    f"3) 实际现金价值=原价×(1-折旧率×月数)={original_value:.2f}×(1-{dep_rate:.4f}×{dep_months:.0f})={result['actual_cash_value']:.2f}。"
                )
                steps.append(f"4) 第三方已赔付={tp_paid:.2f}，扣减后={result['after_third_party']:.2f}。")
                steps.append(f"5) 单件限额={single_limit:.2f}，限额后={result['after_item_limit']:.2f}。")
                steps.append(f"6) 剩余保额={remaining:.2f}，最终赔付={result['final_amount']:.2f}。")
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
        claim_amount = float(claim_info.get("Amount", 0))
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
            "remaining_coverage": coverage_result.get("remaining_amount", 0),
            "final_amount": 0,
            "calculation_steps": "API调用失败",
            "reason": f"API调用失败: {str(e)}",
        }
