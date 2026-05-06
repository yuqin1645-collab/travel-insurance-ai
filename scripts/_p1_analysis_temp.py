import pymysql, json

conn = pymysql.connect(
    host='rds3335l2v6qar8zqontg.mysql.rds.aliyuncs.com',
    port=3306, user='aiuser', password='AI@ssish',
    database='ai', charset='utf8mb4', connect_timeout=10
)
c = conn.cursor()

# 出入境记录缺失
c.execute("""
    SELECT forceid, payout_amount, manual_conclusion, manual_status, remark
    FROM ai_review_result
    WHERE audit_result=%s AND manual_status IN (%s,%s) AND claim_type=%s
      AND remark LIKE %s
    ORDER BY manual_status, payout_amount DESC
""", ('需补齐资料', '通过', '拒绝', 'flight_delay', '%出入境%'))
rows = c.fetchall()
print(f'出入境记录缺失: {len(rows)}件')
for r in rows:
    fid, payout, note, status, remark = r
    print(f'{fid} | 人工={status} | payout={payout}')
    print(f'  AI: {remark[:150]}')
    if note:
        print(f'  人工备注: {note[:120]}')
print()

# 登机牌/行程单缺失
c.execute("""
    SELECT forceid, payout_amount, manual_conclusion, manual_status, remark
    FROM ai_review_result
    WHERE audit_result=%s AND manual_status IN (%s,%s) AND claim_type=%s
      AND (remark LIKE %s OR remark LIKE %s)
      AND remark NOT LIKE %s
    ORDER BY manual_status, payout_amount DESC
""", ('需补齐资料', '通过', '拒绝', 'flight_delay', '%登机牌%', '%行程单%', '%出入境%'))
rows2 = c.fetchall()
print(f'登机牌/行程单缺失: {len(rows2)}件')
for r in rows2:
    fid, payout, note, status, remark = r
    print(f'{fid} | 人工={status} | payout={payout}')
    print(f'  AI: {remark[:150]}')
    if note:
        print(f'  人工备注: {note[:120]}')
print()

# 护照照片页缺失
c.execute("""
    SELECT forceid, payout_amount, manual_conclusion, manual_status, remark
    FROM ai_review_result
    WHERE audit_result=%s AND manual_status IN (%s,%s) AND claim_type=%s
      AND remark LIKE %s
    ORDER BY manual_status, payout_amount DESC
""", ('需补齐资料', '通过', '拒绝', 'flight_delay', '%护照照片页%'))
rows3 = c.fetchall()
print(f'护照照片页缺失: {len(rows3)}件')
for r in rows3:
    fid, payout, note, status, remark = r
    print(f'{fid} | 人工={status} | payout={payout}')
    print(f'  AI: {remark[:150]}')
    if note:
        print(f'  人工备注: {note[:120]}')

conn.close()
