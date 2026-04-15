#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
17个指定案件的完整处理流程
- 能从API下载的 → 下载 + AI审核 + 推送前端 + 写入数据库
- 已入库的 → 补充 claim_status 记录
"""

import sys, os, json, asyncio, aiohttp
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv('.env')

import requests
import pymysql
from scripts.download_claims import ClaimDownloader
from app.claim_ai_reviewer import AIClaimReviewer, review_claim_async
from app.config import config
from app.logging_utils import LOGGER, log_extra as _log_extra
from app.policy_terms_registry import POLICY_TERMS
from app.output.frontend_pusher import push_to_frontend
from app.state.constants import ClaimStatus
from app.production.main_workflow import ProductionWorkflow

API_URL = os.getenv("CLAIMS_API_URL", "https://nanyan.sites.sfcrmapps.cn/services/apexrest/Rest_AI_CLaim")
CLAIMS_DIR = config.CLAIMS_DATA_DIR
REVIEW_DIR = config.REVIEW_RESULTS_DIR


def detect_claim_type(claim_info):
    benefit = str(claim_info.get("BenefitName") or "")
    return "flight_delay" if "延误" in benefit else "baggage_damage"


def get_db_conn():
    return pymysql.connect(
        host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME', 'ai'), charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def claim_exists_in_db(claim_id):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM ai_review_result WHERE claim_id=%s LIMIT 1', (claim_id,))
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists


def register_claim_status(claim_id, forceid, claim_type, current_status):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute('''
        INSERT INTO ai_claim_status (claim_id, forceid, claim_type, current_status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, NOW(), NOW())
        ON DUPLICATE KEY UPDATE updated_at=NOW(), current_status=VALUES(current_status)
    ''', (claim_id, forceid, claim_type, current_status))
    conn.commit()
    cur.close()
    conn.close()


def write_review_result(forceid, result_json, claim_info):
    from app.production.main_workflow import ProductionWorkflow
    wf = ProductionWorkflow()
    fields = wf._extract_review_fields(result_json, claim_info)
    keys = list(fields.keys())
    placeholders = ', '.join(['%s'] * len(keys))
    update_clause = ', '.join([f"{k}=VALUES({k})" for k in keys if k != 'forceid'])
    sql = (f"INSERT INTO ai_review_result ({', '.join(keys)}) "
           f"VALUES ({placeholders}) "
           f"ON DUPLICATE KEY UPDATE {update_clause}, updated_at=CURRENT_TIMESTAMP")

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(sql, list(fields.values()))
    conn.commit()
    cur.close()
    conn.close()


async def process_api_case(case_data, session, reviewer, terms_cache, index, total):
    """处理单个从API下载的案件：审核+推送+入库"""
    forceid = str(case_data.get("forceid") or "").strip()
    claim_id = str(case_data.get("ClaimId") or case_data.get("caseNo") or "").strip()
    benefit = str(case_data.get("BenefitName") or "").strip()
    claim_type = "flight_delay" if "延误" in benefit else "baggage_damage"

    # 下载案件
    print(f"  [{index}/{total}] 下载: {claim_id} (forceid={forceid})")
    downloader = ClaimDownloader(api_url=API_URL, output_dir=str(CLAIMS_DIR))
    downloader.process_claim(case_data)

    # 找案件目录
    claim_folder = None
    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            if str(data.get("forceid") or "") == forceid:
                claim_folder = info_file.parent
                break
        except Exception:
            continue

    if not claim_folder:
        print(f"  [!] 找不到案件目录: {forceid}")
        return None

    print(f"  [{index}/{total}] AI审核: {claim_id}")
    result = None
    for attempt in range(1, 4):
        try:
            result = await review_claim_async(
                reviewer, claim_folder,
                terms_cache.get(claim_type, ""),
                index, total, session
            )
            break
        except Exception as e:
            print(f"  [!] 审核失败 attempt={attempt}: {e}")
            if attempt < 3:
                await asyncio.sleep(3)

    if not result:
        print(f"  [!] 审核彻底失败: {claim_id}")
        return None

    # 保存审核结果文件
    output_dir = REVIEW_DIR / claim_type
    output_dir.mkdir(parents=True, exist_ok=True)
    result_file = output_dir / f"{result['forceid']}_ai_review.json"
    result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [✓] 审核结果已保存: {result_file.name}")

    # 推送前端
    print(f"  [{index}/{total}] 推送前端: {claim_id}")
    push_result = await push_to_frontend(result, session)
    if push_result.get("success"):
        print(f"  [✓] 推送成功: {claim_id}")
    else:
        print(f"  [!] 推送失败: {claim_id} | {str(push_result.get('response',''))[:80]}")

    # 写数据库
    print(f"  [{index}/{total}] 写入数据库: {claim_id}")
    claim_info_for_db = json.loads((claim_folder / "claim_info.json").read_text(encoding="utf-8"))
    register_claim_status(claim_id, forceid, claim_type, ClaimStatus.DOWNLOADED)
    write_review_result(forceid, result, claim_info_for_db)
    print(f"  [✓] 数据库写入完成: {claim_id}")

    return result


async def main():
    target_ids = sorted([
        "202604002170", "202604002169", "202604001960", "202604001959",
        "202604001956", "202604001863", "202604001861", "202604001855",
        "202604001854", "202604001841", "202604001837", "202604001836",
        "202604001835", "202604001831", "202604001830", "202604001829",
        "202604001809",
    ])

    print("=" * 70)
    print(f"批量处理 {len(target_ids)} 个指定案件")
    print("=" * 70)

    # ── 第1步：从API拉全量，匹配目标案件 ──
    print("\n[1/3] 从API拉取案件数据...")
    all_api_records = []
    page = 1
    while True:
        resp = requests.post(API_URL, json={"pageSize": "200", "pageIndex": str(page)}, timeout=60)
        raw = resp.json()
        records = []
        if isinstance(raw, list):
            records = raw
        elif isinstance(raw, dict):
            records = raw.get("data") or raw.get("records") or []
        if not records:
            break
        all_api_records.extend(records)
        tp = raw.get("totalPage", 1)
        tc = raw.get("totalCount", 0)
        print(f"  第{page}页: {len(records)}条 (累计{len(all_api_records)}/{tc})")
        if page >= (tp or 1):
            break
        if len(records) < 200:
            break
        page += 1

    # 匹配
    api_cases = {}
    for r in all_api_records:
        cn = str(r.get("ClaimId", "")).strip()
        if cn in target_ids:
            api_cases[cn] = r

    api_found = sorted(api_cases.keys())
    api_missing = [cid for cid in target_ids if cid not in api_cases]

    print(f"\n  API中找到: {len(api_found)} 个: {api_found}")
    print(f"  API中缺失: {len(api_missing)} 个: {api_missing}")

    # ── 第2步：处理API中的案件 ──
    print(f"\n[2/3] AI审核 + 推送 + 入库 ({len(api_found)} 个)...")

    if api_found:
        reviewer = AIClaimReviewer()
        terms_cache = {}

        # 预加载条款
        for ct in ("flight_delay", "baggage_damage"):
            try:
                tf = POLICY_TERMS.resolve(ct)
                terms_cache[ct] = tf.read_text(encoding="utf-8")
                print(f"  条款加载: {ct}")
            except Exception as e:
                print(f"  条款加载失败: {ct}: {e}")
                terms_cache[ct] = ""

        connector = aiohttp.TCPConnector()
        async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
            for i, claim_id in enumerate(api_found, 1):
                try:
                    await process_api_case(api_cases[claim_id], session, reviewer, terms_cache, i, len(api_found))
                except Exception as e:
                    print(f"  [!] 处理异常 {claim_id}: {e}")
                    import traceback
                    traceback.print_exc()

    # ── 第3步：处理API中缺失的案件（已入库，仅补充 claim_status）──
    print(f"\n[3/3] 补充 claim_status 记录 ({len(api_missing)} 个)...")

    for claim_id in api_missing:
        try:
            conn = get_db_conn()
            cur = conn.cursor()
            cur.execute('''
                SELECT forceid, audit_result FROM ai_review_result WHERE claim_id=%s LIMIT 1
            ''', (claim_id,))
            row = cur.fetchone()
            cur.close()
            conn.close()

            if row:
                # 已有 ai_review_result 记录，仅补充 claim_status
                conn2 = get_db_conn()
                cur2 = conn2.cursor()
                cur2.execute('SELECT 1 FROM ai_claim_status WHERE claim_id=%s LIMIT 1', (claim_id,))
                cs_exists = cur2.fetchone() is not None
                cur2.close()
                conn2.close()

                if not cs_exists:
                    # 找 claim_info
                    claim_folder = None
                    for info_file in CLAIMS_DIR.rglob("claim_info.json"):
                        try:
                            d = json.loads(info_file.read_text(encoding="utf-8"))
                            if str(d.get("forceid") or "") == row['forceid']:
                                claim_folder = info_file.parent
                                benefit = str(d.get("BenefitName") or "")
                                claim_type = "flight_delay" if "延误" in benefit else "baggage_damage"
                                break
                        except Exception:
                            continue

                    if claim_folder:
                        register_claim_status(
                            claim_id, row['forceid'], claim_type,
                            ClaimStatus.DOWNLOADED
                        )
                        print(f"  [✓] claim_status 补充: {claim_id} (forceid={row['forceid']}, AI结论={row['audit_result']})")
                    else:
                        print(f"  [!] claim_info 文件不存在: {claim_id} (forceid={row['forceid']})")
                else:
                    print(f"  [=] claim_status 已存在: {claim_id}")
            else:
                print(f"  [!] 数据库中也没有记录: {claim_id}")
        except Exception as e:
            print(f"  [!] 处理异常 {claim_id}: {e}")

    # ── 最终验证 ──
    print("\n" + "=" * 70)
    print("最终数据验证")
    print("=" * 70)

    conn_final = get_db_conn()
    cur_final = conn_final.cursor()
    cur_final.execute('''
        SELECT claim_id, forceid, audit_result, manual_status, manual_conclusion
        FROM ai_review_result
        WHERE claim_id IN (%s)
        ORDER BY claim_id
    ''' % ','.join(['%s'] * len(target_ids)), target_ids)
    rows = cur_final.fetchall()
    cur_final.execute('''
        SELECT claim_id, forceid, current_status FROM ai_claim_status
        WHERE claim_id IN (%s)
    ''' % ','.join(['%s'] * len(target_ids)), target_ids)
    cs_rows = {r['claim_id']: r for r in cur_final.fetchall()}
    cur_final.close()
    conn_final.close()

    for r in rows:
        cid = r['claim_id']
        cs = cs_rows.get(cid, {})
        print(f"  {cid} | {r['forceid']} | AI:{r['audit_result']} | 人工:{r['manual_status']} | claim_status:{'✓' if cs else '✗'}")

    print(f"\n共处理 {len(rows)} 个案件，全部入库完成。")


if __name__ == "__main__":
    asyncio.run(main())
