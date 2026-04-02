from __future__ import annotations

import re
from typing import Dict, List, Optional


def extract_purchase_amount_and_date(
    ocr_results: Dict,
    *,
    remaining_amount: float = 0.0,
    insured_amount: float = 0.0,
    single_item_limit: float = 1000.0,
) -> Dict[str, Optional[object]]:
    """
    从 OCR 原文中抽取购买凭证的“实付/订单金额”和“成交/支付/下单等时间”。
    原则：金额极其关键，宁缺毋滥；仅在“像购买凭证”的文本中抽取。
    """
    try:
        texts: List[str] = []
        for _fn, _res in (ocr_results or {}).items():
            if not (isinstance(_res, dict) and _res.get("success") and _res.get("text")):
                continue
            t = str(_res.get("text") or "")
            t_compact = re.sub(r"\s+", "", t)
            if any(
                k in t_compact
                for k in (
                    "交易成功",
                    "订单编号",
                    "商品快照",
                    "支付方式",
                    "实付",
                    "付款时间",
                    "支付时间",
                    "成交时间",
                    "下单时间",
                    "拼单时间",
                    "发货时间",
                    "sett:",
                    "sett：",
                )
            ):
                texts.append(t)

        if not texts:
            return {"amount": None, "purchase_date": None, "matched_by": None}

        blob = "\n".join(texts)
        blob_compact = re.sub(r"\s+", "", blob)

        purchase_amt: Optional[float] = None
        matched_by: Optional[str] = None

        def _is_non_price_number(cand: float) -> bool:
            # 排除：单件限额、剩余保额、保额等常见“非价格”
            if abs(cand - float(single_item_limit or 0)) < 0.01:
                return True
            if abs(cand - float(remaining_amount or 0)) < 0.01:
                return True
            if abs(cand - float(insured_amount or 0)) < 0.01:
                return True
            return False

        # 1) “实付/实付款”明确字段（兼容 OCR 将 ￥ 识别成 x/y/v 等）
        m_pay = re.search(
            r"实付(?:款)?(?:共减[^0-9]{0,10}[0-9]{1,6})?[^0-9]{0,10}(?:¥|￥|y|x|v)?([0-9]{1,6}(?:\.[0-9]{1,2})?)",
            blob_compact,
            flags=re.IGNORECASE,
        )
        if m_pay:
            try:
                cand = float(m_pay.group(1))
                if not _is_non_price_number(cand) and 1 <= cand <= 200_000:
                    purchase_amt = cand
                    matched_by = "实付/实付款"
            except Exception:
                pass

        # 2) 兼容：部分 OCR 将“实付：￥104”识别成 “sett: v104 (免运费)”
        if purchase_amt is None:
            m_sett = re.search(
                r"(?:sett|settl|set)[:：]?(?:¥|￥|y|x|v)?([0-9]{1,6}(?:\.[0-9]{1,2})?)\D{0,12}(?:免运费|运费)",
                blob_compact,
                flags=re.IGNORECASE,
            )
            if m_sett:
                try:
                    cand = float(m_sett.group(1))
                    if not _is_non_price_number(cand) and 1 <= cand <= 200_000:
                        purchase_amt = cand
                        matched_by = "sett/交易成功"
                except Exception:
                    pass

        # 日期：支付/成交/下单等 YYYY-MM-DD
        purchase_date: Optional[str] = None
        for m in re.finditer(r"(?:成交时间|付款时间|支付时间)\D{0,10}([0-9]{4}-[0-9]{2}-[0-9]{2})", blob_compact):
            purchase_date = m.group(1)
            break
        if not purchase_date:
            for m in re.finditer(r"(?:下单时间|拼单时间|发货时间|成交时间)\D{0,10}([0-9]{4}-[0-9]{2}-[0-9]{2})", blob_compact):
                purchase_date = m.group(1)
                break

        return {"amount": purchase_amt, "purchase_date": purchase_date, "matched_by": matched_by}
    except Exception:
        return {"amount": None, "purchase_date": None, "matched_by": None}


def extract_third_party_compensation_amount(ocr_results: Dict) -> Dict[str, object]:
    """
    从 OCR 原文中抽取“航司/承运人/第三方已赔付金额”，用于硬扣减。
    重点：过滤日期/手机号/脱敏号等误命中。
    """
    try:
        patterns = [
            r"(?:航司|航空|承运人|airline|carrier).{0,20}?(?:赔付|补偿|赔偿|compensation|paid)\D{0,10}([0-9]{1,6}(?:\.[0-9]{1,2})?)",
            r"(?:已|已向|已获|获得|received).{0,20}?(?:赔付|补偿|赔偿|compensation|paid)\D{0,10}([0-9]{1,6}(?:\.[0-9]{1,2})?)",
            r"(?:赔付|补偿|赔偿|compensation|paid)\D{0,10}([0-9]{1,6}(?:\.[0-9]{1,2})?)\s*(?:元|RMB|CNY|¥|￥|\$)?",
            r"(?:南航|国航|东航|海航|航司|航空|承运人).{0,30}?(?:标准)?(?:赔偿您|赔偿|补偿您|补偿|赔付)\D{0,10}([0-9]{1,6}(?:\.[0-9]{1,2})?)\s*(?:元|RMB|CNY|¥|￥)?",
            # 金额在前、关键词在后（账单/聊天）
            r"(?:¥|￥|\$)?([0-9]{1,6}(?:\.[0-9]{1,2})?)\D{0,20}?(?:元)?\D{0,20}?(?:补偿费|补偿费用|补偿|赔付|赔偿|行李赔偿|行李破损)",
        ]

        matches: List[Dict[str, object]] = []
        candidates: List[float] = []

        def _looks_like_date_around_number(blob: str, g1_start: int, g1_end: int) -> bool:
            after = blob[g1_end : g1_end + 10]
            before = blob[max(0, g1_start - 6) : g1_start]
            if re.match(r"^[/\\-]\\d{1,2}[/\\-]\\d{1,2}", after):
                return True
            if re.match(r"^(?:年)\\d{1,2}(?:月)?", after):
                return True
            if re.match(r"^[/\\-]\\d{1,2}[/\\-]\\d{1,2}\\d{1,2}:\\d{2}", after):
                return True
            if (before.endswith("/") or before.endswith("-")) and re.match(r"^\\d{1,2}", after):
                return True
            return False

        def _has_currency_nearby(blob: str, g1_start: int, g1_end: int) -> bool:
            window = blob[max(0, g1_start - 6) : min(len(blob), g1_end + 6)]
            return bool(re.search(r"(元|rmb|cny|¥|￥|\\$)", window, flags=re.IGNORECASE))

        def _has_comp_context_nearby(blob: str, g1_start: int, g1_end: int) -> bool:
            window = blob[max(0, g1_start - 12) : min(len(blob), g1_end + 12)]
            return bool(re.search(r"(赔付|赔偿|补偿|补偿费|补偿费用|行李赔偿|行李破损)", window))

        def _looks_like_phone_context(blob: str, g1_start: int, g1_end: int) -> bool:
            window = blob[max(0, g1_start - 20) : min(len(blob), g1_end + 30)]
            if re.search(r"(对方账户|手机号|电话|账户|账号|收款方|付款方|持有人|身份证)", window):
                return True
            if "*" in window or "＊" in window:
                return True
            if re.search(r"\\b1[3-9]\\d{2,}\\b", window):
                return True
            return False

        for fn, res in (ocr_results or {}).items():
            if not (isinstance(res, dict) and res.get("success") and res.get("text")):
                continue
            text = str(res.get("text") or "")
            blob_compact = re.sub(r"\\s+", "", text)
            for pat in patterns:
                for m in re.finditer(pat, blob_compact, flags=re.IGNORECASE | re.DOTALL):
                    try:
                        amt = float(m.group(1))
                        g1s, g1e = m.start(1), m.end(1)

                        if _looks_like_date_around_number(blob_compact, g1s, g1e):
                            continue
                        if _looks_like_phone_context(blob_compact, g1s, g1e):
                            continue
                        if amt < 10 and not _has_currency_nearby(blob_compact, g1s, g1e):
                            continue
                        if not _has_currency_nearby(blob_compact, g1s, g1e) and not _has_comp_context_nearby(blob_compact, g1s, g1e):
                            continue

                        if 0 <= amt <= 1_000_000:
                            candidates.append(amt)
                            matches.append(
                                {
                                    "file": str(fn),
                                    "amount": amt,
                                    "snippet": blob_compact[max(0, m.start() - 20) : m.end() + 20],
                                }
                            )
                    except Exception:
                        continue

        if not candidates:
            return {"amount": None, "matches": matches}
        return {"amount": max(candidates), "matches": matches}
    except Exception:
        return {"amount": None, "matches": []}

