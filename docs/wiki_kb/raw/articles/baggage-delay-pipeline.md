---
source_url: https://github.com/yuqin1645-collab/travel-insurance-ai
ingested: 2026-05-05
sha256: 3e8d4af2b7cb973da1c92f1f8415ec89a2d9171cf1ea4a5e8b228a8b1a24370c
---

     1|import re
     2|from pathlib import Path
     3|from typing import Any, Dict, List, Optional
     4|
     5|import aiohttp
     6|
     7|from app.engine.workflow import StageRunner
     8|from app.engine.material_extractor import ExtractionStrategy, MaterialExtractor
     9|from app.logging_utils import LOGGER, log_extra
    10|from app.skills.flight_lookup import get_flight_lookup_skill
    11|from app.vision_preprocessor import prepare_attachments_for_claim
    12|
    13|from app.modules.baggage_delay.stages.utils import (
    14|    _extract_date_yyyy_mm_dd,
    15|    _classify_aviation_failure,
    16|    _extract_file_names,
    17|    _result,
    18|)
    19|from app.modules.baggage_delay.stages.handlers import (
    20|    _check_policy_validity,
    21|    _material_gate,
    22|    _check_special_materials,
    23|    _check_info_consistency,
    24|    _check_airline_baggage_record_exception,
    25|    _check_exclusions,
    26|    _try_transfer_flight_receipt_time,
    27|)
    28|from app.modules.baggage_delay.stages.calculator import (
    29|    _compute_delay_hours_by_rule,
    30|    _compute_payout_with_rules,
    31|    _compute_tier_amount,
    32|)
    33|
    34|
    35|async def review_baggage_delay_async(
    36|    *,
    37|    reviewer: Any,
    38|    claim_folder: Path,
    39|    claim_info: Dict[str, Any],
    40|    policy_terms: str,
    41|    index: int,
    42|    total: int,
    43|    session: aiohttp.ClientSession,
    44|) -> Dict[str, Any]:
    45|    """行李延误审核主流程（编排层）。"""
    46|    forceid = str(claim_info.get("forceid") or "unknown")
    47|    description = str(claim_info.get("Description_of_Accident") or "")
    48|    assessment = str(claim_info.get("Assessment_Remark") or "")
    49|    text_blob = f"{description}\n{assessment}".strip()
    50|    file_names = _extract_file_names(claim_info)
    51|
    52|    debug: Dict[str, Any] = {
    53|        "policy_terms_excerpt": (policy_terms or "")[:1000],
    54|        "claim_folder": str(claim_folder),
    55|        "file_count": len(file_names),
    56|        "file_names_sample": file_names[:10],
    57|        "debug": [],
    58|    }
    59|    runner = StageRunner(ctx=debug, forceid=forceid)
    60|
    61|    LOGGER.info(
    62|        f"[{index}/{total}] 行李延误审核开始",
    63|        extra=log_extra(forceid=forceid, stage="baggage_delay_start", attempt=0),
    64|    )
    65|    conclusions: List[Dict[str, str]] = []
    66|
    67|    # 0) 视觉识别
    68|    vision_extract: Dict[str, Any] = {}
    69|    try:
    70|        extractor = MaterialExtractor(reviewer=reviewer, forceid=forceid)
    71|        extraction = await extractor.extract(
    72|            claim_folder=claim_folder,
    73|            claim_info=claim_info,
    74|            strategy=ExtractionStrategy.VISION_DIRECT,
    75|            prompt_name="00_vision_extract",
    76|            session=session,
    77|        )
    78|        raw_vision = extraction.vision_data
    79|        if isinstance(raw_vision, dict):
    80|            vision_extract = raw_vision
    81|        elif isinstance(raw_vision, list) and raw_vision and isinstance(raw_vision[0], dict):
    82|            vision_extract = raw_vision[0]
    83|        LOGGER.info(
    84|            f"[{index}/{total}] 视觉识别完成: has_boarding={vision_extract.get('has_boarding_or_ticket')} "
    85|            f"has_delay_proof={vision_extract.get('has_baggage_delay_proof')} "
    86|            f"has_receipt_proof={vision_extract.get('has_baggage_receipt_time_proof')}",
    87|            extra=log_extra(forceid=forceid, stage="baggage_delay_vision", attempt=0),
    88|        )
    89|    except Exception as _ve:
    90|        LOGGER.warning(
    91|            f"[{index}/{total}] 视觉识别失败（降级到纯文本）: {_ve}",
    92|            extra=log_extra(forceid=forceid, stage="baggage_delay_vision", attempt=0),
    93|        )
    94|    debug["vision_extract"] = vision_extract
    95|
    96|    # 0.5) AI结构化抽取
    97|    ai_parsed, parse_err = await runner.run(
    98|        "baggage_delay_parse",
    99|        reviewer._ai_baggage_delay_parse_async,
   100|        claim_info,
   101|        text_blob,
   102|        session=session,
   103|        max_retries=2,
   104|        retry_sleep=2.0,
   105|    )
   106|    if parse_err:
   107|        debug["parse_warning"] = str(parse_err)[:200]
   108|    if isinstance(ai_parsed, dict):
   109|        debug["ai_parsed"] = ai_parsed
   110|
   111|    # 合并视觉识别结果到 ai_parsed
   112|    if vision_extract and isinstance(ai_parsed, dict):
   113|        for key in (
   114|            "has_boarding_or_ticket", "has_baggage_delay_proof", "has_baggage_receipt_time_proof",
   115|            "has_baggage_tag_proof",
   116|            "has_airline_baggage_record", "airline_baggage_record_name",
   117|            "airline_baggage_record_flight", "airline_baggage_record_pieces",
   118|            "flight_actual_arrival_time", "baggage_receipt_time", "receipt_times", "delay_hours",
   119|            "has_id_proof", "has_passport", "has_exit_entry_record", "exit_datetime",
   120|            "has_bank_card_proof", "risk_flags",
   121|            "all_flights_found",
   122|        ):
   123|            vision_val = vision_extract.get(key)
   124|            parsed_val = ai_parsed.get(key)
   125|            if vision_val is not None and str(vision_val).lower() not in ("unknown", "", "[]"):
   126|                ai_parsed[key] = vision_val
   127|            elif parsed_val is None:
   128|                ai_parsed[key] = vision_val
   129|        for key in ("flight_no", "flight_date", "dep_iata", "arr_iata"):
   130|            vision_val = vision_extract.get(key)
   131|            if vision_val and str(vision_val).lower() not in ("unknown", ""):
   132|                existing = ai_parsed.get(key)
   133|                if existing is None or str(existing).lower() in ("unknown", ""):
   134|                    ai_parsed[key] = vision_val
   135|
   136|        # 安全网：交叉校验
   137|        proof_source = vision_extract.get("baggage_delay_proof_source") or ""
   138|        if proof_source and str(proof_source).lower() not in ("unknown", ""):
   139|            hd_val = ai_parsed.get("has_baggage_delay_proof")
   140|            if not hd_val or str(hd_val).lower() == "false":
   141|                ai_parsed["has_baggage_delay_proof"] = True
   142|                debug.setdefault("auto_corrected", []).append("has_baggage_delay_proof: PIR报告存在但 vision 误判为 false，已自动纠正")
   143|            ht_val = ai_parsed.get("has_baggage_tag_proof")
   144|            if not ht_val or str(ht_val).lower() == "false":
   145|                ai_parsed["has_baggage_tag_proof"] = True
   146|                debug.setdefault("auto_corrected", []).append("has_baggage_tag_proof: PIR报告含航班+行李信息，等效行李牌，已自动纠正")
   147|
   148|        receipt_time = vision_extract.get("baggage_receipt_time") or ""
   149|        if receipt_time and str(receipt_time).lower() not in ("unknown", ""):
   150|            low_confidence_markers = ["/unknown", "/未知", "~", "约", "左右", "estimated", "大概"]
   151|            is_low_confidence = any(m in str(receipt_time) for m in low_confidence_markers)
   152|            if not is_low_confidence:
   153|                hr_val = ai_parsed.get("has_baggage_receipt_time_proof")
   154|                if not hr_val or str(hr_val).lower() == "false":
   155|                    ai_parsed["has_baggage_receipt_time_proof"] = True
   156|                    debug.setdefault("auto_corrected", []).append("has_baggage_receipt_time_proof: 签收时间已提取但 vision 误判为 false，已自动纠正")
   157|            else:
   158|                ai_parsed["baggage_receipt_time"] = None
   159|                # 同时清除 delay_hours：无有效签收时间时，delay_hours 是模型估算值，不可靠
   160|                ai_parsed["delay_hours"] = None
   161|                debug.setdefault("auto_corrected", []).append(f"baggage_receipt_time: 清除低置信度时间值 {receipt_time}")
   162|
   163|        vision_notes = str(vision_extract.get("notes") or "").strip()
   164|
   165|        # 校验：如果 vision notes 明确说行李延误证明缺失，纠正 has_baggage_delay_proof 为 false
   166|        # Vision 模型有时会因 PIR 报告或物品清单而判定 has_baggage_delay_proof=True，
   167|        # 但 notes 中又明确说"行李延误证明文件缺失"——这是矛盾的，应以 notes 为准
   168|        delay_proof_missing_markers = [
   169|            "行李延误证明文件缺失", "行李延误证明缺失", "行李延误证明.*缺失",
   170|            "未见行李延误证明", "无行李延误证明",
   171|        ]
   172|        for marker in delay_proof_missing_markers:
   173|            if re.search(marker, vision_notes):
   174|                if ai_parsed.get("has_baggage_delay_proof") not in (None, False):
   175|                    ai_parsed["has_baggage_delay_proof"] = False
   176|                    ai_parsed["delay_hours"] = None
   177|                    debug.setdefault("auto_corrected", []).append(
   178|                        f"has_baggage_delay_proof: vision notes明确行李延误证明缺失，纠正为 false"
   179|                    )
   180|                break
   181|
   182|        # 校验：如果 vision notes 明确说明签收时间来自航空公司邮件通知/转运航班预计到达时间，
   183|        # 说明并非真正的行李签收证明，应将 has_baggage_receipt_time_proof 纠正为 false
   184|        receipt_time_email_markers = [
   185|            "航空公司邮件", "邮件通知", "邮件预计", "邮件预计",
   186|            "转运航班", "行李搭乘", "预计.*到达", "行李将搭乘",
   187|            "luggage will arrive", "baggage will arrive",
   188|        ]
   189|        if ai_parsed.get("has_baggage_receipt_time_proof") and vision_notes:
   190|            for marker in receipt_time_email_markers:
   191|                if re.search(marker, vision_notes):
   192|                    ai_parsed["has_baggage_receipt_time_proof"] = False
   193|                    ai_parsed["baggage_receipt_time"] = None
   194|                    ai_parsed["delay_hours"] = None
   195|                    debug["no_receipt_proof_confirmed"] = True
   196|                    debug.setdefault("auto_corrected", []).append(
   197|                        f"has_baggage_receipt_time_proof: vision notes明确时间来自邮件/转运航班，非实际签收证明，纠正为 false"
   198|                    )
   199|                    break
   200|
   201|        # PIR二次聚焦提取
   202|        needs_pir_extract = (
   203|            ai_parsed.get("has_baggage_delay_proof") is True
   204|            and not debug.get("no_receipt_proof_confirmed")
   205|            and (not ai_parsed.get("baggage_receipt_time")
   206|                 or str(ai_parsed.get("baggage_receipt_time")).lower() in ("unknown", ""))
   207|            and (not ai_parsed.get("delay_hours")
   208|                 or str(ai_parsed.get("delay_hours")).lower() in ("unknown", ""))
   209|        )
   210|        if needs_pir_extract:
   211|            try:
   212|                processed_attachments, _ = prepare_attachments_for_claim(
   213|                    claim_folder, claim_info=claim_info, max_attachments=0
   214|                )
   215|                attachment_paths = [a.path for a in processed_attachments]
   216|                if not attachment_paths:
   217|                    debug["pir_receipt_extract"] = {"attempted": False, "reason": "无可用图片附件"}
   218|                else:
   219|                    pir_extract = await reviewer._ai_pir_receipt_time_extract_async(
   220|                        attachment_paths=attachment_paths,
   221|                        claim_info=claim_info,
   222|                        session=session,
   223|                    )
   224|                    if isinstance(pir_extract, dict):
   225|                        receipt = pir_extract.get("baggage_receipt_time")
   226|                        confidence = str(pir_extract.get("confidence") or "").lower()
   227|                        if receipt and str(receipt).lower() not in ("unknown", "") and confidence in ("high", "medium"):
   228|                            ai_parsed["baggage_receipt_time"] = receipt
   229|                            if pir_extract.get("receipt_times"):
   230|                                ai_parsed["receipt_times"] = pir_extract["receipt_times"]
   231|                            pir_delay = pir_extract.get("delay_hours")
   232|                            if pir_delay and str(pir_delay).lower() != "unknown":
   233|                                ai_parsed["delay_hours"] = pir_delay
   234|                            debug.setdefault("auto_corrected", []).append(
   235|                                f"baggage_receipt_time: PIR二次提取成功 {receipt}（置信度: {confidence}）"
   236|                            )
   237|                        else:
   238|                            debug["pir_receipt_extract"] = {
   239|                                "attempted": True, "result": "未提取到有效签收时间",
   240|                                "confidence": confidence,
   241|                            }
   242|            except Exception as e:
   243|                debug["pir_receipt_extract_warning"] = str(e)[:200]
   244|
   245|    elif vision_extract and not isinstance(ai_parsed, dict):
   246|        ai_parsed = dict(vision_extract)
   247|
   248|    # 前置准入校验
   249|    policy_violation = _check_policy_validity(claim_info, debug, vision_extract=vision_extract)
   250|    if policy_violation:
   251|        conclusions.append({"checkpoint": "前置准入", "Eligible": "否", "Remark": policy_violation})
   252|        policy_action = debug.get("policy_validity_action", "reject")
   253|        if policy_action == "supplement":
   254|            return _result(forceid, policy_violation, "S", conclusions, debug)
   255|        return _result(forceid, policy_violation, "N", conclusions, debug)
   256|
   257|    # 身份一致性校验
   258|    identity_violation = _check_info_consistency(claim_info, ai_parsed or {})
   259|    if identity_violation:
   260|        conclusions.append({"checkpoint": "身份一致性", "Eligible": "否", "Remark": identity_violation})
   261|        return _result(forceid, identity_violation, "N", conclusions, debug)
   262|
   263|    # 免责条款校验
   264|    exclusion_reason = _check_exclusions(claim_info, text_blob, ai_parsed or {})
   265|    if exclusion_reason:
   266|        conclusions.append({"checkpoint": "免责条款", "Eligible": "否", "Remark": exclusion_reason})
   267|        return _result(forceid, f"拒赔：{exclusion_reason}", "N", conclusions, debug)
   268|
   269|    # 官方航班数据补强
   270|    aviation_lookup: Dict[str, Any] = {}
   271|    try:
   272|        if isinstance(ai_parsed, dict):
   273|            flight_no = str(ai_parsed.get("flight_no") or "").strip()
   274|            dep_iata = str(ai_parsed.get("dep_iata") or "").strip().upper()
   275|            arr_iata = str(ai_parsed.get("arr_iata") or "").strip().upper()
   276|            flight_date = (
   277|                _extract_date_yyyy_mm_dd(ai_parsed.get("flight_date"))
   278|                or _extract_date_yyyy_mm_dd(claim_info.get("Date_of_Accident"))
   279|            )
   280|            if flight_no and flight_date:
   281|                skill = get_flight_lookup_skill()
   282|                aviation_lookup = await skill.lookup_status(
   283|                    flight_no=flight_no,
   284|                    date=flight_date,
   285|                    dep_iata=dep_iata if dep_iata and dep_iata != "UNKNOWN" else None,
   286|                    arr_iata=arr_iata if arr_iata and arr_iata != "UNKNOWN" else None,
   287|                    session=session,
   288|                )
   289|                if aviation_lookup.get("success"):
   290|                    actual_arr = aviation_lookup.get("actual_arr")
   291|                    if actual_arr:
   292|                        ai_parsed["flight_actual_arrival_time"] = actual_arr
   293|                        debug["arrival_source"] = "variflight_actual_arr"
   294|                else:
   295|                    debug["arrival_source"] = "material_or_llm_fallback"
   296|    except Exception as e:
   297|        debug["aviation_lookup_warning"] = str(e)[:200]
   298|    debug["aviation_lookup"] = aviation_lookup
   299|    aviation_failure_type = _classify_aviation_failure(aviation_lookup)
   300|    debug["aviation_failure_type"] = aviation_failure_type
   301|    if aviation_failure_type == "system_error":
   302|        conclusions.append(
   303|            {"checkpoint": "官方航班数据", "Eligible": "需人工判断",
   304|             "Remark": f"官方航班查询异常: {str(aviation_lookup.get('error') or '')[:120]}"}
   305|        )
   306|    elif aviation_failure_type == "evidence_gap":
   307|        conclusions.append(
   308|            {"checkpoint": "官方航班数据", "Eligible": "需补齐资料",
   309|             "Remark": "官方航班数据未命中，需补充可核验航班号/日期/航段信息"}
   310|        )
   311|    elif aviation_lookup.get("success") is True:
   312|        conclusions.append(
   313|            {"checkpoint": "官方航班数据", "Eligible": "是",
   314|             "Remark": "已获取官方实际到达时间用于时长核算"}
   315|        )
   316|
   317|    # 转运航班到达时间回退
   318|    transfer_flight_debug = await _try_transfer_flight_receipt_time(
   319|        ai_parsed or {}, vision_extract, session,
   320|    )
   321|    debug["transfer_flight_receipt"] = transfer_flight_debug
   322|
   323|    # 事故类型校验
   324|    parsed_accident_type = str((ai_parsed or {}).get("accident_type") or "").strip().lower()
   325|    if parsed_accident_type == "baggage_loss" or (("行李丢失" in text_blob) and ("延误" not in text_blob)):
   326|        conclusions.append({"checkpoint": "事故类型", "Eligible": "否", "Remark": "事故为行李丢失，需转随身财产损失责任"})
   327|        return _result(forceid, "拒赔：事故类型为行李丢失，非托运行李延误责任", "N", conclusions, debug)
   328|    conclusions.append({"checkpoint": "事故类型", "Eligible": "是", "Remark": "未发现行李丢失单独触发，继续按行李延误审核"})
   329|
   330|    # 材料门禁
   331|    missing_materials: List[str] = []
   332|    if isinstance(ai_parsed, dict):
   333|        def _has_flag(key: str) -> str:
   334|            return str(ai_parsed.get(key) or "unknown").strip().lower()
   335|
   336|        flag = _has_flag("has_boarding_or_ticket")
   337|        if flag == "false":
   338|            missing_materials.append("交通票据（机票/登机牌/行程单）")
   339|        elif flag == "unknown":
   340|            if not any(w in f"{text_blob} {' '.join(file_names)}".lower()
   341|                       for w in ["机票", "登机牌", "行程单", "ticket", "boarding", "itinerary"]):
   342|                missing_materials.append("交通票据（机票/登机牌/行程单）")
   343|
   344|        delay_proof_flag = _has_flag("has_baggage_delay_proof")
   345|        receipt_proof_flag = _has_flag("has_baggage_receipt_time_proof")
   346|        joined_text = f"{text_blob} {' '.join(file_names)}".lower()
   347|        delay_proof_kw = any(w in joined_text for w in ["行李延误", "行李不正常", "pir", "baggage delay", "delay proof", "property irregularity"])
   348|        receipt_proof_kw = any(w in joined_text for w in ["签收", "领取", "receipt", "delivered", "delivery"])
   349|
   350|        has_delay_proof = delay_proof_flag == "true" or (delay_proof_flag == "unknown" and delay_proof_kw)
   351|        has_receipt_proof = receipt_proof_flag == "true" or (receipt_proof_flag == "unknown" and receipt_proof_kw)
   352|
   353|        if not has_delay_proof and not has_receipt_proof:
   354|            missing_materials.append("行李延误证明或行李签收单（航空公司出具的行李延误时数/原因书面证明，或含具体签收时间的行李签收单，二选一）")
   355|
   356|        tag_flag = _has_flag("has_baggage_tag_proof")
   357|        if tag_flag == "unknown":
   358|            v_tag = str(vision_extract.get("has_baggage_tag_proof") or "unknown").strip().lower()
   359|            if v_tag not in ("unknown", ""):
   360|                tag_flag = v_tag
   361|
   362|        if tag_flag in ("false", "unknown"):
   363|            exception_met = _check_airline_baggage_record_exception(
   364|                vision_extract, ai_parsed or {}, claim_info, joined_text
   365|            )
   366|            if exception_met:
   367|                debug["baggage_tag_exception"] = "航空公司官方行李记录满足替代条件，视同行李牌已提供"
   368|            else:
   369|                missing_materials.append("托运行李牌照片（含姓名、航班信息、行李牌号码）")
   370|
   371|        id_flag = _has_flag("has_id_proof")
   372|        passport_flag = _has_flag("has_passport")
   373|        if id_flag == "false" and passport_flag == "false":
   374|            missing_materials.append("被保险人身份证正反面或护照")
   375|        if passport_flag == "false" and id_flag in ("false", "unknown"):
   376|            missing_materials.append("护照照片页、签证页、出入境盖章页")
   377|
   378|        bank_flag = _has_flag("has_bank_card_proof")
   379|        if bank_flag == "false":
   380|            debug["bank_card_warning"] = "视觉识别未见银行卡信息，建议人工确认打款账号"
   381|
   382|        special_needs = _check_special_materials(claim_info, text_blob, file_names)
   383|        missing_materials.extend(special_needs)
   384|        missing_materials = sorted(set(missing_materials))
   385|    else:
   386|        missing_materials = _material_gate(text_blob, file_names)
   387|
   388|    debug["missing_materials"] = missing_materials
   389|    if missing_materials:
   390|        conclusions.append({"checkpoint": "材料完整性", "Eligible": "需补齐资料", "Remark": "；".join(missing_materials)})
   391|        return _result(forceid, "需补齐资料：" + "；".join(missing_materials), "Y", conclusions, debug)
   392|    conclusions.append({"checkpoint": "材料完整性", "Eligible": "是", "Remark": "视觉识别确认关键材料已提供"})
   393|
   394|    # 人工复核触发
   395|    manual_flags = []
   396|    manual_keywords = ["手写", "多语言", "伪造", "ps", "涂改", "矛盾", "争议", "模糊"]
   397|    for kw in manual_keywords:
   398|        if kw in text_blob.lower():
   399|            manual_flags.append(kw)
   400|    parsed_risk = str((ai_parsed or {}).get("manual_review_risk") or "").strip().lower()
   401|    if parsed_risk and parsed_risk not in {"none", "unknown"}:
   402|        manual_flags.append(parsed_risk)
   403|    if manual_flags:
   404|        debug["manual_review_flags"] = manual_flags
   405|        conclusions.append({"checkpoint": "人工复核触发", "Eligible": "需人工判断", "Remark": f"命中关键词: {','.join(manual_flags)}"})
   406|        return _result(forceid, "转人工复核：存在材料识别或真实性争议", "Y", conclusions, debug)
   407|
   408|    # 延误时长核算与门槛
   409|    delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
   410|    delay_hours = delay_calc.get("delay_hours")
   411|    if debug.get("transfer_flight_receipt", {}).get("receipt_time_set"):
   412|        delay_calc["receipt_time_source"] = "transfer_flight_arrival"
   413|        delay_calc = _compute_delay_hours_by_rule(ai_parsed or {}, text_blob)
   414|        delay_hours = delay_calc.get("delay_hours")
   415|        delay_hours_str = f"{delay_hours:.2f}小时" if delay_hours is not None else "未知"
   416|        conclusions.append({
   417|            "checkpoint": "行李签收时间",
   418|            "Eligible": "需补齐资料",
   419|            "Remark": f"以行李签收证明中的明确日期/时间为准；无签收证明时，以后续转运航班到达时间为辅助参考，待补件后按实际签收时间修正。当前估算延误时长{delay_hours_str}。",
   420|        })
   421|        return _result(
   422|            forceid,
   423|            f"需补齐资料：行李签收证明（含签收时间），当前以后续转运航班到达时间辅助参考，估算行李延误{delay_hours_str}，待补件后按实际签收时间修正。",
   424|            "Y", conclusions, debug,
   425|        )
   426|    debug["delay_calc"] = delay_calc
   427|    if delay_hours is None:
   428|        if aviation_failure_type == "system_error":
   429|            return _result(forceid, "转人工复核：官方航班数据查询异常，无法完成时长核算", "Y", conclusions, debug)
   430|        conclusions.append({"checkpoint": "延误时长", "Eligible": "需补齐资料", "Remark": "未识别到明确延误时长或签收时间信息"})
   431|        return _result(forceid, "需补齐资料：请补充行李签收证明（含签收时间）或承运人出具的行李延误时长证明", "Y", conclusions, debug)
   432|    if delay_hours < 6:
   433|        conclusions.append({"checkpoint": "赔付门槛", "Eligible": "否", "Remark": f"延误时长{delay_hours:.2f}小时，未达到6小时"})
   434|        return _result(forceid, "拒赔：行李延误时长未达到6小时赔付门槛", "N", conclusions, debug)
   435|    conclusions.append({"checkpoint": "赔付门槛", "Eligible": "是", "Remark": f"延误时长{delay_hours:.2f}小时，达到赔付门槛"})
   436|
   437|    # 信息一致性校验
   438|    consistency_violation = _check_info_consistency(claim_info, ai_parsed or {})
   439|    if consistency_violation:
   440|        conclusions.append({"checkpoint": "信息一致性", "Eligible": "否", "Remark": consistency_violation})
   441|        return _result(forceid, consistency_violation, "N", conclusions, debug)
   442|
   443|    # AI审核意见
   444|    ai_audit, audit_err = await runner.run(
   445|        "baggage_delay_audit",
   446|        reviewer._ai_baggage_delay_audit_async,
   447|        claim_info,
   448|        {
   449|            "delay_hours": delay_hours,
   450|            "missing_materials": missing_materials,
   451|            "manual_flags": manual_flags,
   452|            "rule_conclusions": conclusions,
   453|            "ai_parsed": ai_parsed or {},
   454|        },
   455|        policy_terms or "",
   456|        session=session,
   457|        max_retries=1,
   458|        retry_sleep=1.0,
   459|    )
   460|    if audit_err:
   461|        debug["audit_warning"] = str(audit_err)[:200]
   462|    elif isinstance(ai_audit, dict):
   463|        ai_audit["delay_hours"] = delay_hours
   464|        ai_audit["tier_amount"] = _compute_tier_amount(delay_hours)
   465|        debug["ai_audit"] = ai_audit
   466|        ai_audit_result = str(ai_audit.get("audit_result") or "").strip()
   467|        ai_missing = [m for m in (ai_audit.get("missing_materials") or []) if m and str(m).strip()]
   468|        if ai_missing:
   469|            vision_confirmed = set()
   470|            if isinstance(ai_parsed, dict):
   471|                if str(ai_parsed.get("has_boarding_or_ticket") or "").strip().lower() in ("true",):
   472|                    vision_confirmed.update(["登机牌", "电子客票", "行程单", "机票"])
   473|                if str(ai_parsed.get("has_baggage_delay_proof") or "").lower() in ("true",):
   474|                    vision_confirmed.update(["行李延误证明", "行李不正常", "PIR"])
   475|                if str(ai_parsed.get("has_baggage_receipt_time_proof") or "").lower() in ("true",):
   476|                    vision_confirmed.update(["行李签收证明", "签收单"])
   477|                if str(ai_parsed.get("has_baggage_tag_proof") or "").lower() in ("true",):
   478|                    vision_confirmed.add("托运行李牌")
   479|            filtered_missing = []
   480|            for item in ai_missing:
   481|                if any(kw in item for kw in vision_confirmed):
   482|                    continue
   483|                if item not in set(missing_materials):
   484|                    missing_materials.append(item)
   485|            missing_materials = sorted(set(missing_materials))
   486|            debug["missing_materials"] = missing_materials
   487|        if ai_audit_result == "需补齐资料" and missing_materials:
   488|            conclusions.append({"checkpoint": "AI审计补件", "Eligible": "需补齐资料", "Remark": "；".join(missing_materials)})
   489|            return _result(forceid, "需补齐资料：" + "；".join(missing_materials), "Y", conclusions, debug)
   490|        elif ai_audit_result == "拒绝":
   491|            reason = str(ai_audit.get("reason") or ai_audit.get("explanation") or "AI审核拒赔")
   492|            conclusions.append({"checkpoint": "AI审计", "Eligible": "否", "Remark": reason})
   493|            return _result(forceid, f"拒赔：{reason}", "N", conclusions, debug)
   494|
   495|    # 赔付核算
   496|    from app.modules.baggage_delay.stages.utils import _safe_float
   497|    claim_amount = _safe_float(claim_info.get("Amount"))
   498|    insured_amount = _safe_float(claim_info.get("Insured_Amount"))
   499|    remaining_coverage = _safe_float(claim_info.get("Remaining_Coverage"))
   500|    cap = None
   501|