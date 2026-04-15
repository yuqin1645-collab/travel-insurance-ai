#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
同步本地审核结果JSON到数据库 ai_review_result 表
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import aiomysql
import asyncio


def parse_json_file(json_path: Path) -> dict:
    """解析审核结果JSON文件"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  Error reading {json_path}: {e}")
        return None


def load_claim_info(forceid: str) -> dict:
    """从 claim_info.json 加载原始案件信息"""
    claims_dir = Path('claims_data')
    for info_file in claims_dir.rglob("claim_info.json"):
        try:
            with open(info_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if data.get('forceid') == forceid:
                    return data
        except:
            continue
    return {}


def extract_fields(data: dict, claim_info: dict = None) -> dict:
    """从JSON提取字段到数据库字段"""
    result = {
        'forceid': data.get('forceid', ''),
        'claim_id': data.get('claim_id', ''),
    }

    # flight_delay_audit 部分
    audit = data.get('flight_delay_audit', {})
    if audit:
        # 标准化审核结果
        audit_result = audit.get('audit_result', '')

        result['audit_result'] = audit_result
        result['audit_status'] = 'completed' if audit.get('audit_result') else 'pending'
        result['confidence_score'] = audit.get('confidence_score')
        result['audit_time'] = datetime.now()
        result['auditor'] = 'AI系统'

        # 逻辑校验
        logic_check = audit.get('logic_check', {})
        result['identity_match'] = 'Y' if logic_check.get('identity_match') else 'N'
        result['threshold_met'] = 'Y' if logic_check.get('threshold_met') else 'N'
        result['exclusion_triggered'] = 'Y' if logic_check.get('exclusion_triggered') else 'N'
        result['exclusion_reason'] = logic_check.get('exclusion_reason', '')

        # key_data
        key_data = audit.get('key_data', {})
        result['passenger_name'] = key_data.get('passenger_name', '')
        result['delay_duration_minutes'] = key_data.get('delay_duration_minutes')
        result['delay_reason'] = key_data.get('reason', '')

        # payout
        payout = audit.get('payout_suggestion', {})
        result['payout_amount'] = payout.get('amount')
        result['payout_currency'] = payout.get('currency', 'CNY')
        result['payout_basis'] = payout.get('basis', '')

    # DebugInfo 部分
    debug_info = data.get('DebugInfo', {})

    # flight_delay_parse - 最完整的数据源
    parse = debug_info.get('flight_delay_parse', {})
    if parse:
        # 乘客信息
        passenger = parse.get('passenger', {})
        if not result.get('passenger_name'):
            result['passenger_name'] = passenger.get('name', '')
        result['passenger_id_type'] = passenger.get('id_type', '')
        result['passenger_id_number'] = passenger.get('id_number', '')

        # 保单信息
        policy_hint = parse.get('policy_hint', {})
        result['policy_no'] = policy_hint.get('policy_no', '')
        result['insurer'] = policy_hint.get('insurer', '')
        result['policy_effective_date'] = policy_hint.get('policy_effective_date')
        result['policy_expiry_date'] = policy_hint.get('policy_expiry_date')

        # 航班信息
        flight = parse.get('flight', {})
        if not result.get('flight_no'):
            result['flight_no'] = flight.get('ticket_flight_no') or flight.get('operating_flight_no', '')
        result['operating_carrier'] = flight.get('operating_carrier', '')

        # 航线信息
        route = parse.get('route', {})
        if not result.get('dep_iata'):
            result['dep_iata'] = route.get('dep_iata', '')
        if not result.get('arr_iata'):
            result['arr_iata'] = route.get('arr_iata', '')
        result['dep_city'] = route.get('dep_city', '')
        result['arr_city'] = route.get('arr_city', '')

        # 时间信息
        schedule = parse.get('schedule_local', {})
        actual = parse.get('actual_local', {})
        alt = parse.get('alternate_local', {})

        result['planned_dep_time'] = parse_datetime(schedule.get('planned_dep'))
        result['planned_arr_time'] = parse_datetime(schedule.get('planned_arr'))
        result['actual_dep_time'] = parse_datetime(actual.get('actual_dep'))
        result['actual_arr_time'] = parse_datetime(actual.get('actual_arr'))
        result['alt_dep_time'] = parse_datetime(alt.get('alt_dep'))
        result['alt_arr_time'] = parse_datetime(alt.get('alt_arr'))

    # flight_delay_aviation_lookup - 航班数据补充
    lookup = debug_info.get('flight_delay_aviation_lookup', {})
    if lookup:
        if not result.get('flight_no'):
            result['flight_no'] = lookup.get('flight_no', '')
        if not result.get('dep_iata'):
            result['dep_iata'] = lookup.get('dep_iata', '')
        if not result.get('arr_iata'):
            result['arr_iata'] = lookup.get('arr_iata', '')
        if not result.get('planned_dep_time'):
            result['planned_dep_time'] = parse_datetime(lookup.get('planned_dep'))
        if not result.get('planned_arr_time'):
            result['planned_arr_time'] = parse_datetime(lookup.get('planned_arr'))
        if not result.get('actual_dep_time'):
            result['actual_dep_time'] = parse_datetime(lookup.get('actual_dep'))
        if not result.get('actual_arr_time'):
            result['actual_arr_time'] = parse_datetime(lookup.get('actual_arr'))
        if not result.get('delay_duration_minutes'):
            result['delay_duration_minutes'] = lookup.get('delay_minutes')
        # 状态
        if lookup.get('status'):
            if lookup.get('status') == '取消':
                result['delay_type'] = 'cancelled'

    # flight_delay_vision_extract - 视觉提取补充
    vision = debug_info.get('flight_delay_vision_extract', {})
    if vision:
        flights = vision.get('all_flights_found', [])
        if flights and not result.get('flight_no'):
            first = flights[0]
            result['flight_no'] = first.get('flight_no', '')
            if not result.get('dep_iata'):
                result['dep_iata'] = first.get('dep_iata', '')
            if not result.get('arr_iata'):
                result['arr_iata'] = first.get('arr_iata', '')

    # flight_delay_payout - 赔付信息补充
    payout_info = debug_info.get('flight_delay_payout', {})
    if payout_info:
        if not result.get('insured_amount'):
            result['insured_amount'] = payout_info.get('insured_amount')
        if not result.get('remaining_coverage'):
            result['remaining_coverage'] = payout_info.get('remaining_coverage')

    # claim_info - 原始案件信息（从本地文件加载）
    if claim_info:
        if not result.get('insured_amount'):
            result['insured_amount'] = claim_info.get('Insured_Amount') or claim_info.get('Amount')
        if not result.get('remaining_coverage'):
            result['remaining_coverage'] = claim_info.get('Remaining_Coverage')
        if not result.get('policy_no'):
            result['policy_no'] = claim_info.get('PolicyNo')
        if not result.get('insurer'):
            result['insurer'] = claim_info.get('Insurance_Company')
        if not result.get('passenger_name'):
            result['passenger_name'] = claim_info.get('Applicant_Name')
        if not result.get('claim_id'):
            result['claim_id'] = claim_info.get('ClaimId')

    # 基础字段
    result['remark'] = data.get('Remark', '')[:2000] if data.get('Remark') else ''
    result['is_additional'] = data.get('IsAdditional', 'N')
    result['supplementary_count'] = 0  # 默认值
    result['key_conclusions'] = json.dumps(data.get('KeyConclusions', []), ensure_ascii=False)
    result['decision_reason'] = audit.get('explanation', '') if audit else ''
    result['raw_result'] = json.dumps(data, ensure_ascii=False)

    return result


def parse_datetime(value):
    """解析日期时间字符串"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        # ISO格式: 2026-03-22T00:05:00+02:00
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except:
        return None


async def sync_to_database(limit: int = None):
    """同步本地JSON到数据库"""
    # 数据库连接
    conn = await aiomysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        db=os.getenv('DB_NAME'),
        charset='utf8mb4',
        autocommit=True
    )

    # 获取所有本地JSON文件
    json_dir = Path('review_results/flight_delay')
    json_files = list(json_dir.glob('*_ai_review.json'))

    if limit:
        json_files = json_files[:limit]

    print(f"Found {len(json_files)} JSON files to sync")

    success = 0
    fail = 0
    skip = 0

    async with conn.cursor() as cursor:
        for i, json_file in enumerate(json_files):
            try:
                data = parse_json_file(json_file)
                if not data:
                    fail += 1
                    continue

                forceid = data.get('forceid')
                if not forceid:
                    fail += 1
                    continue

                # 加载原始案件信息
                claim_info = load_claim_info(forceid)

                # 提取字段
                fields = extract_fields(data, claim_info)

                # 检查是否已存在
                await cursor.execute(
                    "SELECT COUNT(*) FROM ai_review_result WHERE forceid = %s",
                    (forceid,)
                )
                exists = (await cursor.fetchone())[0] > 0

                if exists:
                    # 更新现有记录
                    set_clause = ', '.join([f"{k} = %s" for k in fields.keys()])
                    values = list(fields.values())
                    values.append(forceid)
                    sql = f"UPDATE ai_review_result SET {set_clause}, updated_at = NOW() WHERE forceid = %s"
                    await cursor.execute(sql, values)
                    skip += 1
                else:
                    # 插入新记录
                    keys = list(fields.keys())
                    placeholders = ', '.join(['%s'] * len(keys))
                    sql = f"INSERT INTO ai_review_result ({', '.join(keys)}) VALUES ({placeholders})"
                    await cursor.execute(sql, list(fields.values()))
                    success += 1

                if (i + 1) % 50 == 0:
                    print(f"  Progress: {i + 1}/{len(json_files)}")

            except Exception as e:
                fail += 1
                print(f"  Error: {json_file.name}: {e}")

    conn.close()

    print()
    print("=" * 50)
    print(f"Sync complete:")
    print(f"  Success: {success}")
    print(f"  Updated: {skip}")
    print(f"  Failed: {fail}")
    print("=" * 50)


async def verify_sync():
    """验证同步结果"""
    conn = await aiomysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT')),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        db=os.getenv('DB_NAME'),
        charset='utf8mb4'
    )

    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM ai_review_result")
        total = (await cursor.fetchone())[0]

        await cursor.execute("SELECT COUNT(*) FROM ai_review_result WHERE audit_result IS NOT NULL AND audit_result != ''")
        has_audit = (await cursor.fetchone())[0]

        await cursor.execute("SELECT COUNT(*) FROM ai_review_result WHERE passenger_name IS NOT NULL AND passenger_name != ''")
        has_name = (await cursor.fetchone())[0]

        await cursor.execute("SELECT COUNT(*) FROM ai_review_result WHERE flight_no IS NOT NULL AND flight_no != ''")
        has_flight = (await cursor.fetchone())[0]

        await cursor.execute("SELECT COUNT(*) FROM ai_review_result WHERE delay_duration_minutes IS NOT NULL")
        has_delay = (await cursor.fetchone())[0]

        await cursor.execute("SELECT COUNT(*) FROM ai_review_result WHERE payout_amount IS NOT NULL")
        has_payout = (await cursor.fetchone())[0]

    conn.close()

    print()
    print("Database sync status:")
    print(f"  Total records: {total}")
    print(f"  Has audit_result: {has_audit}")
    print(f"  Has passenger_name: {has_name}")
    print(f"  Has flight_no: {has_flight}")
    print(f"  Has delay_duration: {has_delay}")
    print(f"  Has payout_amount: {has_payout}")


if __name__ == '__main__':
    limit = None
    if len(sys.argv) > 1:
        limit = int(sys.argv[1])

    print("Syncing local JSON files to database...")
    asyncio.run(sync_to_database(limit))
    asyncio.run(verify_sync())
