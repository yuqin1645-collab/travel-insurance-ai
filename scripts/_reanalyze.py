#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pymysql
from collections import Counter
from app.config import config

conn = pymysql.connect(
    host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER,
    password=config.DB_PASSWORD, database=config.DB_NAME, charset='utf8mb4'
)
cur = conn.cursor()

cur.execute('SELECT COUNT(*) FROM ai_review_result')
total = cur.fetchone()[0]
print(f'总案件数: {total}')

cur.execute('SELECT claim_type, COUNT(*) FROM ai_review_result GROUP BY claim_type')
for row in cur.fetchall():
    print(f'  {row[0]}: {row[1]}')

cur.execute("""SELECT COUNT(*) FROM ai_review_result
WHERE audit_result IS NOT NULL AND audit_result != ''
AND manual_status IS NOT NULL AND manual_status != ''
AND manual_status != '待定'
AND (is_additional != 1 OR is_additional IS NULL)""")
matched = cur.fetchone()[0]
print(f'\nAI+人工均已处理(排除待定): {matched}')

cur.execute("""SELECT COUNT(*) FROM ai_review_result
WHERE audit_result IS NOT NULL AND audit_result != ''
AND manual_status IS NOT NULL AND manual_status != ''
AND manual_status != '待定'
AND (is_additional != 1 OR is_additional IS NULL)
AND (
    (audit_result = '通过' AND manual_status = '通过')
    OR (audit_result = '拒绝' AND manual_status = '拒绝')
    OR (audit_result = '需补齐资料' AND manual_status = '需补齐资料')
)""")
consistent = cur.fetchone()[0]
print(f'一致: {consistent}/{matched} = {consistent/matched*100:.1f}%')

# P0
cur.execute("""SELECT forceid, claim_type, audit_result, manual_status, payout_amount, remark
FROM ai_review_result
WHERE audit_result = '通过' AND manual_status = '拒绝'
AND (is_additional != 1 OR is_additional IS NULL)""")
p0 = cur.fetchall()
print(f'\n=== P0 (AI通过/人工拒绝): {len(p0)} ===')
for r in p0:
    remark = (r[5] or '')[:100]
    print(f'  {r[0]} | {r[1]} | PAY={r[4]} | {remark}')

# P1
cur.execute("""SELECT forceid, claim_type, audit_result, manual_status, payout_amount
FROM ai_review_result
WHERE audit_result = '需补齐资料'
AND (manual_status = '通过' OR manual_status = '拒绝')
AND (is_additional != 1 OR is_additional IS NULL)""")
p1 = cur.fetchall()
print(f'\n=== P1 (AI补件/人工通过或拒绝): {len(p1)} ===')
p1_flight = [r for r in p1 if r[1] == 'flight_delay']
p1_baggage = [r for r in p1 if r[1] == 'baggage_delay']
print(f'  flight_delay: {len(p1_flight)}')
print(f'  baggage_delay: {len(p1_baggage)}')

# P2
cur.execute("""SELECT forceid, claim_type, audit_result, manual_status, payout_amount
FROM ai_review_result
WHERE audit_result = '拒绝' AND manual_status = '通过'
AND (is_additional != 1 OR is_additional IS NULL)""")
p2 = cur.fetchall()
print(f'\n=== P2 (AI拒绝/人工通过): {len(p2)} ===')
p2_flight = [r for r in p2 if r[1] == 'flight_delay']
p2_baggage = [r for r in p2 if r[1] == 'baggage_delay']
print(f'  flight_delay: {len(p2_flight)}')
print(f'  baggage_delay: {len(p2_baggage)}')

# P2 flight with payout>0
p2_paid = []
for r in p2_flight:
    try:
        amt = float(str(r[4]).replace(',', '')) if r[4] else 0
        if amt > 0:
            p2_paid.append(r)
    except:
        pass
print(f'  flight_delay 有实际赔付: {len(p2_paid)}')

# Cross-tab
cur.execute("""SELECT audit_result, manual_status, COUNT(*) FROM ai_review_result
WHERE audit_result IS NOT NULL AND audit_result != ''
AND manual_status IS NOT NULL AND manual_status != ''
AND manual_status != '待定'
AND (is_additional != 1 OR is_additional IS NULL)
GROUP BY audit_result, manual_status ORDER BY COUNT(*) DESC""")
print('\n=== AI vs 人工 交叉表 ===')
for row in cur.fetchall():
    print(f'  AI={row[0]} | 人工={row[1]} | {row[2]}件')

conn.close()
