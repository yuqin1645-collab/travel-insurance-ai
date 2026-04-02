#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成人工审核 vs AI 审核对比报表 (xlsx)

来源:
- 人工审核结果: static/随身财产责任案件量（截止0310 1704）.xls
- AI 审核结果: review_results/*_ai_review.json
- ClaimId 映射: claims_data/**/claim_info.json 中的 ClaimId + forceid

输出:
- static/随身财产责任案件量_AI评估.xlsx

用法:
  py scripts/export_ai_vs_manual_report.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_claimid_to_forceid(claims_dir: Path) -> Dict[str, str]:
    """遍历 claims_data 下所有案件，构建 ClaimId -> forceid 映射"""
    mapping: Dict[str, str] = {}
    if not claims_dir.is_dir():
        return mapping

    for info_file in claims_dir.rglob("claim_info.json"):
        sub = info_file.parent
        if sub.name.startswith("."):
            continue
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        claim_id = (data.get("ClaimId") or data.get("claimId") or "").strip()
        forceid = (data.get("forceid") or "").strip()
        if claim_id and forceid:
            mapping[claim_id] = forceid
    return mapping


def load_ai_results(review_dir: Path) -> Dict[str, Dict[str, Any]]:
    """加载 review_results 下的 *_ai_review.json，键为 forceid"""
    results: Dict[str, Dict[str, Any]] = {}
    if not review_dir.is_dir():
        return results
    for f in review_dir.rglob("*_ai_review.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        fid = str(data.get("forceid") or "").strip()
        if fid:
            results[fid] = data
    return results


def classify_ai_decision(remark: str, is_additional: str) -> str:
    """根据 Remark + IsAdditional 粗分 AI 决策类型"""
    r = remark or ""
    flag = (is_additional or "").upper()
    if flag == "Y":
        if "需要补充材料" in r:
            return "AI-补件"
        return "AI-人工/待补件"
    # N: 终结
    if r.startswith("拒赔") or "拒赔" in r:
        return "AI-拒赔"
    # 仅当明确“审核通过/同意赔付”且不是“无可赔付金额”时，才视为赔付通过
    if (("审核通过" in r or "同意赔付" in r) and "无可赔付金额" not in r):
        return "AI-赔付/通过"
    return "AI-终结(其他)"


def main() -> int:
    # 路径
    eccs_path = ROOT / "static" / "随身财产责任案件量（截止0310 1704）.xls"
    claims_dir = ROOT / "claims_data"
    review_dir = ROOT / "review_results"
    output_path = ROOT / "static" / "随身财产责任案件量_AI评估.xlsx"

    print("=" * 60)
    print("生成 人工 vs AI 审核对比报表")
    print("=" * 60)
    print(f"ECCS 人工结果: {eccs_path}")
    print(f"claims_data 目录: {claims_dir}")
    print(f"AI 结果目录: {review_dir}")
    print()

    # 1) 读取人工结果 (HTML/table 形式的 xls)
    if not eccs_path.exists():
        print("找不到 ECCS 人工结果文件 (.xls)")
        return 1

    tables = pd.read_html(str(eccs_path), encoding="utf-8")
    if not tables:
        print("未能从 .xls 中解析出表格。")
        return 1
    df = tables[0]

    # 统一列名（第一列应为子理赔案件号）
    if df.columns.size < 1:
        print("人工结果表列数异常。")
        return 1

    first_col = df.columns[0]
    # 为了稳妥，保留原列名，同时创建一个标准列名视图
    claim_id_col = str(first_col)

    # 2) 构建 ClaimId -> forceid 映射 & 加载 AI 结果
    claimid_to_forceid = load_claimid_to_forceid(claims_dir)
    ai_by_forceid = load_ai_results(review_dir)

    print(f"从 claims_data 读取到 {len(claimid_to_forceid)} 个 ClaimId 映射")
    print(f"从 review_results 读取到 {len(ai_by_forceid)} 条 AI 结果\n")

    # 3) 为每一行匹配 AI 结果并填列
    ai_forceids = []
    ai_is_additional = []
    ai_remark = []
    ai_decision = []

    for _, row in df.iterrows():
        claim_id = str(row.get(claim_id_col) or "").strip()
        fid = claimid_to_forceid.get(claim_id)
        if not fid:
            ai_forceids.append("")
            ai_is_additional.append("")
            ai_remark.append("")
            ai_decision.append("")
            continue
        res = ai_by_forceid.get(fid, {})
        remark = str(res.get("Remark") or "")
        is_add = str(res.get("IsAdditional") or "")
        ai_forceids.append(fid)
        ai_is_additional.append(is_add)
        ai_remark.append(remark)
        ai_decision.append(classify_ai_decision(remark, is_add))

    df["AI_forceid"] = ai_forceids
    df["AI_IsAdditional"] = ai_is_additional
    df["AI_Remark"] = ai_remark
    df["AI_决策类型"] = ai_decision

    # 4) 导出为 xlsx
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(".tmp.xlsx")
    try:
        # 先写到临时文件，避免写到一半失败导致旧文件损坏
        df.to_excel(tmp_path, index=False)
        # Windows 下 rename 在目标文件被占用时会抛 PermissionError
        os.replace(tmp_path, output_path)
    except PermissionError:
        # 目标文件正在被 Excel/WPS 打开时无法覆盖，降级：输出带时间戳的新文件
        ts = time.strftime("%Y%m%d_%H%M%S")
        fallback_path = output_path.with_name(f"{output_path.stem}_{ts}{output_path.suffix}")
        try:
            if tmp_path.exists():
                os.replace(tmp_path, fallback_path)
            else:
                df.to_excel(fallback_path, index=False)
        except Exception:
            # 确保不遗留 tmp 文件
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except Exception:
                pass
            raise
        print(f"目标文件被占用(可能正在被 Excel/WPS 打开)，无法覆盖写入: {output_path}")
        print(f"已改为输出新文件: {fallback_path}")
        print("如需覆盖写入，请先关闭打开的 xlsx 文件后重试。")
    finally:
        # 清理临时文件（如果仍存在）
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass

    print(f"已生成报表: {output_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

