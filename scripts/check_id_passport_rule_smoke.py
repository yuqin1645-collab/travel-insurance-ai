#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path
from typing import Dict, List, Any


def apply_id_passport_rule(
    id_type: str,
    merged_present: Dict[str, Any],
    missing_before: List[str],
) -> List[str]:
    """
    根据当前代码中的统一规则，模拟后处理结果：
    - 身份证投保：默认移除护照/签证/出入境相关缺件
    - 身份证投保且仅有签章页(visa_entry_exit=true, passport=false)：补“被保险人护照照片页”
    - 非身份证投保：保留原护照相关缺件
    """
    is_id_card_policy = "身份证" in str(id_type or "")
    has_passport = bool(merged_present.get("passport"))
    has_visa_entry_exit = bool(merged_present.get("visa_entry_exit"))

    processed: List[str] = []
    for item in missing_before:
        text = str(item)
        is_passport_related = (
            ("出入境" in text) or ("护照类" in text) or ("护照" in text) or ("签证" in text)
        )
        if is_passport_related and is_id_card_policy:
            continue
        processed.append(text)

    if is_id_card_policy and has_visa_entry_exit and (not has_passport):
        tip = "被保险人护照照片页"
        if not any("护照照片页" in x for x in processed):
            processed.append(tip)

    return processed


def assert_case(case: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    name = case.get("name", "unknown")
    hint = case.get("input_hint", {}) or {}
    expected = case.get("expected", {}) or {}

    missing_after = apply_id_passport_rule(
        id_type=str(hint.get("ID_Type", "")),
        merged_present=hint.get("merged_present", {}) or {},
        missing_before=hint.get("missing_materials_before_postprocess", []) or [],
    )

    should_contain = expected.get("should_contain_missing", []) or []
    should_not_contain = expected.get("should_not_contain_missing", []) or []

    for token in should_contain:
        if not any(str(token) in str(x) for x in missing_after):
            errs.append(f"[{name}] 预期包含 `{token}`，实际: {missing_after}")

    for token in should_not_contain:
        if any(str(token) in str(x) for x in missing_after):
            errs.append(f"[{name}] 预期不包含 `{token}`，实际: {missing_after}")

    return errs


def main() -> int:
    sample_file = Path("/home/travel-insurance-ai/review_results/_runner/id_passport_rule_smoke_cases.json")
    if not sample_file.exists():
        print(f"[ERROR] 样例文件不存在: {sample_file}")
        return 2

    data = json.loads(sample_file.read_text(encoding="utf-8"))
    cases = data.get("cases", []) or []
    if not cases:
        print("[ERROR] 未找到可执行样例 cases")
        return 2

    all_errs: List[str] = []
    for case in cases:
        all_errs.extend(assert_case(case))

    if all_errs:
        print("[FAIL] 身份证/护照补件规则校验失败")
        for e in all_errs:
            print(" -", e)
        return 1

    print(f"[PASS] 规则校验通过，共 {len(cases)} 条样例")
    return 0


if __name__ == "__main__":
    sys.exit(main())
