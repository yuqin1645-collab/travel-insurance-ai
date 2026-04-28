#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
审核结果回传模块
将AI审核结果推送到前端API
"""

import os
import sys
import json
import asyncio
import logging
import aiohttp
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

LOGGER = logging.getLogger(__name__)

# API配置
FRONTEND_API_URL = os.getenv('FRONTEND_API_URL', 'https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim_Conclusion')
FRONTEND_API_KEY = os.getenv('FRONTEND_API_KEY', '')
FRONTEND_TIMEOUT = int(os.getenv('FRONTEND_TIMEOUT', '30'))


def map_audit_result_to_status(audit_result: str) -> str:
    """
    将审核结果映射为审批状态

    Args:
        audit_result: 审核结果 (通过/拒绝/需补件/supplementary_needed等)

    Returns:
        审批状态: "1"=通过, "0"=拒赔, "2"=补件
    """
    if not audit_result:
        return "0"  # 默认拒赔

    audit_result = str(audit_result).strip().lower()

    if audit_result in ['通过', 'approved', 'pass']:
        return "1"  # 审批通过
    elif audit_result in ['拒绝', 'rejected', '拒赔']:
        return "0"  # 拒赔
    elif audit_result in ['需补件', '补件', 'supplementary_needed', '需补齐资料']:
        return "2"  # 补件
    else:
        return "0"  # 默认拒赔


def map_flight_status(status: str) -> str:
    """
    映射航班状态

    Args:
        status: 航班状态文本

    Returns:
        状态码: "0"=正常, "1"=延误, "2"=取消, "3"=备降
    """
    if not status:
        return "1"  # 默认延误

    status = str(status).strip().lower()

    if status in ['正常', 'normal', '已到达']:
        return "0"
    elif status in ['延误', 'delayed', 'delay']:
        return "1"
    elif status in ['取消', 'cancelled', 'cancel']:
        return "2"
    elif status in ['备降', 'diverted', 'alternate']:
        return "3"
    else:
        return "1"  # 默认延误


def build_flights_from_json(data: Dict) -> List[Dict]:
    """
    从审核结果JSON提取航班信息

    Args:
        data: 审核结果JSON

    Returns:
        航班信息列表
    """
    flights = []

    # 从 DebugInfo 中提取航班信息
    debug_info = data.get('DebugInfo') or {}

    # 1. 从 flight_delay_aviation_lookup 提取
    lookup = debug_info.get('flight_delay_aviation_lookup') or {}
    if lookup:
        flight = {
            "flight_no": lookup.get('flight_no', ''),
            "airline_name": '',
            "dep_city_name": '',
            "dep_airport_name": lookup.get('dep_iata', ''),
            "dep_time": format_datetime(lookup.get('planned_dep')),
            "actual_dep_time": format_datetime(lookup.get('actual_dep')),
            "arr_city_name": '',
            "arr_airport_name": lookup.get('arr_iata', ''),
            "arr_time": format_datetime(lookup.get('planned_arr')),
            "actual_arr_time": format_datetime(lookup.get('actual_arr')),
            "flight_date": extract_date(lookup.get('planned_dep')),
            "flight_status": map_flight_status(lookup.get('status')),
            "remark": '',
            "flight_Order": "1"
        }
        flights.append(flight)

    # 2. 从 flight_delay_vision_extract 提取所有航班
    vision = debug_info.get('flight_delay_vision_extract') or {}
    all_flights = vision.get('all_flights_found', [])

    for i, f in enumerate(all_flights):
        # 检查是否已存在相同航班号
        existing = [x for x in flights if x['flight_no'] == f.get('flight_no')]
        if existing:
            continue

        flight = {
            "flight_no": f.get('flight_no', ''),
            "airline_name": '',
            "dep_city_name": '',
            "dep_airport_name": f.get('dep_iata', ''),
            "dep_time": f.get('date', ''),
            "actual_dep_time": '',
            "arr_city_name": '',
            "arr_airport_name": f.get('arr_iata', ''),
            "arr_time": '',
            "actual_arr_time": '',
            "flight_date": extract_date(f.get('date')),
            "flight_status": "1",  # 默认延误
            "remark": f.get('role_hint', ''),
            "flight_Order": str(len(flights) + 1)
        }
        flights.append(flight)

    return flights


def build_conclusions_from_json(data: Dict) -> List[Dict]:
    """
    从审核结果JSON提取审核结论

    Args:
        data: 审核结果JSON

    Returns:
        审核结论列表
    """
    conclusions = []

    key_conclusions = data.get('KeyConclusions', [])
    for kc in key_conclusions:
        conclusion = {
            "checkpoint": kc.get('checkpoint', ''),
            "Eligible": kc.get('Eligible', ''),
            "Remark": kc.get('Remark', '')
        }
        conclusions.append(conclusion)

    return conclusions


def format_datetime(dt_str: Any) -> str:
    """格式化日期时间"""
    if not dt_str:
        return ''

    if isinstance(dt_str, datetime):
        return dt_str.strftime('%Y-%m-%d %H:%M:%S')

    dt_str = str(dt_str)

    # ISO格式: 2026-03-22T00:05:00+02:00
    try:
        if 'T' in dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OverflowError):
        pass

    return dt_str


def extract_date(dt_str: Any) -> str:
    """从日期时间中提取日期"""
    if not dt_str:
        return ''

    dt_str = str(dt_str)

    # 尝试提取日期部分
    if 'T' in dt_str:
        return dt_str.split('T')[0]
    elif ' ' in dt_str:
        return dt_str.split(' ')[0]

    # 可能已经是日期格式
    if len(dt_str) >= 10 and dt_str[4] == '-':
        return dt_str[:10]

    return dt_str


def build_api_payload(data: Dict) -> Dict:
    """
    构建API请求体

    Args:
        data: 审核结果JSON

    Returns:
        API请求体
    """
    forceid = data.get('forceid', '')
    claim_id = data.get('ClaimId', '') or data.get('claimId', '') or data.get('claim_id', '')
    remark = data.get('Remark', '')
    is_additional = data.get('IsAdditional', 'N')

    # 从 flight_delay_audit 或 baggage_delay_audit 提取审核信息
    audit = data.get('flight_delay_audit') or data.get('baggage_delay_audit') or {}
    audit_result = audit.get('audit_result', '')
    explanation = audit.get('explanation', '')

    # 映射审批状态
    approval_status = map_audit_result_to_status(audit_result)

    # 提取赔付金额（文本格式，两位小数）
    payout_amount = ""
    payout = audit.get('payout_suggestion') or {}
    raw_amount = payout.get('amount')
    if raw_amount is not None:
        try:
            payout_amount = f"{float(raw_amount):.2f}"
        except (ValueError, TypeError):
            payout_amount = ""

    # 构建 payload
    payload = {
        "forceid": forceid,
        "ClaimId": claim_id,
        "Remark": remark,
        "Assessment_Remark": "",
        "Reimbursement_Rejection": "",
        "IsAdditional": approval_status,
        "Amount": payout_amount,
        "Supplementary_Reason": "",
        "sd_cause": "",
        "Ofline_online": "",
        "Remark_Category": "",
        "Conclusions": build_conclusions_from_json(data),
        "flights": build_flights_from_json(data)
    }

    # 根据审核结果填充不同字段
    if approval_status == "1":
        # 通过：Assessment_Remark 填判定理由
        payload["Assessment_Remark"] = explanation or remark

    elif approval_status == "0":
        # 拒绝：Assessment_Remark 和 Reimbursement_Rejection 填拒赔理由
        payload["Assessment_Remark"] = explanation or remark
        payload["Reimbursement_Rejection"] = explanation or remark

    elif approval_status == "2":
        # 补件：Assessment_Remark 和 Reimbursement_Rejection 不填
        # 补件信息填写到专属字段
        debug_info = data.get('DebugInfo') or {}
        materials = debug_info.get('materials') or {}

        # 补件清单
        missing_materials = materials.get('missing_materials', [])
        payload["Supplementary_Reason"] = '; '.join(missing_materials) if missing_materials else explanation

        # 补件原因
        manual_review_reason = materials.get('manual_review_reason', '')
        payload["sd_cause"] = manual_review_reason or explanation

        # 线上线下补件：根据申请金额判断
        # 从 claim_info 提取申请金额
        claim_info = data.get('claim_info') or {}
        insured_amount = float(claim_info.get('Insured_Amount') or claim_info.get('insured_amount') or 0)
        payload["Ofline_online"] = "Offline" if insured_amount > 5000 else "Online"

        # 补件协助类型：航延默认客服联系客户补充
        payload["Remark_Category"] = "客服联系客户补充（发邮件给客户）"

    return payload


async def push_to_frontend(
    data: Dict,
    session: Optional[aiohttp.ClientSession] = None
) -> Dict:
    """
    推送审核结果到前端API

    Args:
        data: 审核结果JSON
        session: aiohttp会话

    Returns:
        推送结果
    """
    forceid = data.get('forceid', 'unknown')

    # 构建请求体
    payload = build_api_payload(data)

    close_session = False
    if session is None:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=FRONTEND_TIMEOUT),
            trust_env=True,
        )
        close_session = True

    headers = {
        "Content-Type": "application/json; charset=utf-8",
    }

    if FRONTEND_API_KEY:
        headers["Authorization"] = f"Bearer {FRONTEND_API_KEY}"

    try:
        # API需要数组格式 [...]
        payload_list = [payload]

        async with session.post(
            FRONTEND_API_URL,
            headers=headers,
            json=payload_list  # 发送数组格式
        ) as response:
            response_text = await response.text()

            result = {
                "forceid": forceid,
                "status_code": response.status,
                "success": response.status == 200,
                "response": response_text[:500] if response_text else "",
                "payload": payload
            }

            if response.status == 200:
                LOGGER.info(f"推送成功: {forceid}")
            else:
                LOGGER.warning(f"推送失败 ({response.status}): {forceid}, 响应: {response_text[:200]}")

            return result

    except Exception as e:
        LOGGER.error(f"推送异常: {forceid}, {str(e)[:100]}")
        return {
            "forceid": forceid,
            "status_code": 0,
            "success": False,
            "response": str(e),
            "payload": payload
        }

    finally:
        if close_session:
            await session.close()


async def push_from_json_file(
    json_path: Path,
    session: Optional[aiohttp.ClientSession] = None
) -> Dict:
    """
    从JSON文件读取并推送

    Args:
        json_path: JSON文件路径
        session: aiohttp会话

    Returns:
        推送结果
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return await push_to_frontend(data, session)

    except Exception as e:
        LOGGER.error(f"读取文件失败: {json_path}: {e}")
        return {
            "forceid": "",
            "status_code": 0,
            "success": False,
            "response": str(e),
            "payload": {}
        }


async def batch_push_from_directory(
    directory: Path = None,
    limit: int = None
) -> Dict:
    """
    批量推送目录下所有审核结果

    Args:
        directory: 目录路径
        limit: 限制数量

    Returns:
        批量推送结果
    """
    if directory is None:
        directory = Path('review_results/flight_delay')

    # 获取所有JSON文件
    json_files = list(directory.glob('*_ai_review.json'))

    if limit:
        json_files = json_files[:limit]

    LOGGER.info(f"发现 {len(json_files)} 个文件待推送")

    # 创建会话
    session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=FRONTEND_TIMEOUT),
        trust_env=True,
    )

    results = {
        "total": len(json_files),
        "success": 0,
        "failed": 0,
        "details": []
    }

    try:
        for i, json_file in enumerate(json_files):
            LOGGER.info(f"[{i+1}/{len(json_files)}] 推送: {json_file.name}")

            result = await push_from_json_file(json_file, session)
            results["details"].append(result)

            if result["success"]:
                results["success"] += 1
            else:
                results["failed"] += 1

            # 避免请求过快
            await asyncio.sleep(0.5)

    finally:
        await session.close()

    LOGGER.info(f"批量推送完成: 总计={results['total']}, 成功={results['success']}, 失败={results['failed']}")

    return results


# 命令行入口
if __name__ == '__main__':
    if len(sys.argv) > 1:
        # 推送指定文件
        json_file = Path(sys.argv[1])
        result = asyncio.run(push_from_json_file(json_file))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 批量推送
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
        result = asyncio.run(batch_push_from_directory(limit=limit))
        print(json.dumps(result, ensure_ascii=False, indent=2))