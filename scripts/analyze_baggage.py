#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行李延误专项分析 - 数据库驱动"""

import os, json, random
import pymysql
from dotenv import load_dotenv
load_dotenv()

def get_conn():
    return pymysql.connect(
        host=os.getenv('DB_HOST', ''),
        port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER', ''),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'ai'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )

def analyze():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 总体统计
            cur.execute("SELECT COUNT(*) as cnt FROM ai_review_result WHERE claim_type='baggage_delay'")
            total = cur.fetchone()['cnt']

            cur.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN audit_result = manual_status THEN 1 ELSE 0 END) as match_count
                FROM ai_review_result
                WHERE claim_type='baggage_delay' AND manual_status IS NOT NULL
            """)
            comp = cur.fetchone()
            comparable = comp['total']
            matched = comp['match_count']
            rate = matched / comparable * 100 if comparable > 0 else 0

            print("=" * 60)
            print("行李延误 Database Snapshot")
            print("=" * 60)
            print(f"数据库总行李延误案件数: {total}")
            print(f"可比案件数(人工已处理): {comparable}")
            print(f"当前一致率(数据库真实): {rate:.1f}%")
            print(f"一致数: {matched} / 不一致数: {comparable - matched}")
            print()

            # P0: AI通过 vs 人工拒绝/补件
            cur.execute("""
                SELECT forceid, audit_result, manual_status, manual_conclusion, remark, audit_reason_tags, key_conclusions, payout_amount
                FROM ai_review_result
                WHERE claim_type='baggage_delay'
                AND audit_result='通过'
                AND manual_status IN ('拒绝', '需补齐资料', '需补件')
            """)
            p0 = cur.fetchall()

            # 分类P0
            p0_cancel = [r for r in p0 if r.get('manual_conclusion') and '取消' in r['manual_conclusion']]
            p0_30day = [r for r in p0 if r.get('manual_conclusion') and '30天' in r['manual_conclusion']]
            p0_real = [r for r in p0 if r not in p0_cancel and r not in p0_30day]

            print(f"P0 (AI通过 vs 人工拒绝/补件): {len(p0)}")
            print(f"  - 用户取消理赔: {len(p0_cancel)} (非AI错误)")
            print(f"  - 超30天未补件自动关案: {len(p0_30day)} (业务流程)")
            print(f"  - 真实P0: {len(p0_real)}")
            for r in p0_real:
                print(f"    {r['forceid']}: manual={r['manual_status']}, remark={(r['remark'] or '')[:80]}")
                print(f"      manual_conclusion={(r['manual_conclusion'] or '')[:80]}")
            print()

            # P1: AI补件 vs 人工通过/拒绝
            cur.execute("""
                SELECT forceid, audit_result, manual_status, manual_conclusion, remark, audit_reason_tags, key_conclusions, payout_amount
                FROM ai_review_result
                WHERE claim_type='baggage_delay'
                AND audit_result IN ('需补齐资料', '需补件')
                AND manual_status IN ('通过', '拒绝')
            """)
            p1 = cur.fetchall()

            p1_pass = [r for r in p1 if r['manual_status'] == '通过']
            p1_reject = [r for r in p1 if r['manual_status'] == '拒绝']

            print(f"P1 (AI补件 vs 人工通过/拒绝): {len(p1)}")
            print(f"  - 人工通过: {len(p1_pass)}")
            print(f"  - 人工拒绝: {len(p1_reject)}")

            # P1 补件原因分类
            p1_baggage_sign = [r for r in p1 if '签收' in (r['remark'] or '')]
            p1_transport = [r for r in p1 if '交通' in (r['remark'] or '') or '票据' in (r['remark'] or '')]
            p1_other_mat = [r for r in p1 if r not in p1_baggage_sign and r not in p1_transport]

            print(f"  - 行李签收证明: {len(p1_baggage_sign)}")
            print(f"  - 交通票据: {len(p1_transport)}")
            print(f"  - 其他材料: {len(p1_other_mat)}")

            for r in p1:
                print(f"    {r['forceid']}: manual={r['manual_status']}, remark={(r['remark'] or '')[:100]}")
            print()

            # P2: AI拒绝 vs 人工通过/补件
            cur.execute("""
                SELECT forceid, audit_result, manual_status, manual_conclusion, remark, audit_reason_tags, key_conclusions, payout_amount
                FROM ai_review_result
                WHERE claim_type='baggage_delay'
                AND audit_result='拒绝'
                AND manual_status IN ('通过', '需补齐资料', '需补件')
            """)
            p2 = cur.fetchall()

            p2_pass = [r for r in p2 if r['manual_status'] == '通过']
            p2_supplement = [r for r in p2 if r['manual_status'] in ('需补齐资料', '需补件')]

            print(f"P2 (AI拒绝 vs 人工通过/补件): {len(p2)}")
            print(f"  - 人工通过: {len(p2_pass)}")
            print(f"  - 人工补件: {len(p2_supplement)}")

            # P2 拒赔原因精细分类
            reasons = {}
            reason_map = {}  # forceid -> reason
            for r in p2:
                remark = r['remark'] or ''
                tags = r['audit_reason_tags'] or ''
                combined = remark + ' ' + tags

                if '延误未达' in combined or '6h' in combined or '6小时' in combined or '6 小时' in combined:
                    cat = '延误未达6h门槛'
                elif '国内航班' in combined or '纯国内' in combined:
                    cat = '纯国内航班'
                elif '行李丢失' in combined or '事故类型为行李丢失' in combined or '行李消失' in combined:
                    cat = '事故类型为行李丢失'
                elif '有效期' in combined or '超出' in combined:
                    cat = '超出保单有效期'
                elif '海关' in combined or '没收' in combined or '扣留' in combined:
                    cat = '海关没收/扣留'
                elif '恐怖' in combined:
                    cat = '恐怖活动'
                elif '未通知' in combined:
                    cat = '未通知承运人'
                elif '重复' in combined:
                    cat = '重复索赔'
                else:
                    cat = '其他'

                reasons[cat] = reasons.get(cat, 0) + 1
                reason_map[r['forceid']] = cat

            print("\nP2 拒赔原因分布:")
            for k, v in sorted(reasons.items(), key=lambda x: -x[1]):
                print(f"  {k}: {v}")

            # 按人工状态细分
            print("\nP2 按拒赔原因+人工状态细分:")
            for cat in sorted(reasons.keys(), key=lambda x: -reasons[x]):
                cat_rows = [r for r in p2 if reason_map[r['forceid']] == cat]
                by_manual = {}
                for r in cat_rows:
                    ms = r['manual_status']
                    by_manual[ms] = by_manual.get(ms, 0) + 1
                detail = ', '.join(f"{ms}: {c}" for ms, c in by_manual.items())
                print(f"  {cat} ({reasons[cat]}): {detail}")

            # 输出所有P2 forceid列表
            print("\nP2 forceid列表:")
            for r in p2:
                cat = reason_map[r['forceid']]
                print(f"  [{cat}] {r['forceid']} | manual={r['manual_status']} | {(r['remark'] or '')[:70]}")

            # 随机抽取2个P2和2个P1样例
            print("\n" + "=" * 60)
            print("随机抽取样例进行深度分析")
            print("=" * 60)

            random.seed(42)
            p2_samples = random.sample(p2, min(2, len(p2)))
            p1_samples = random.sample(p1, min(2, len(p1)))

            print("\n【P2 样例 1】")
            r = p2_samples[0]
            print(f"  forceid: {r['forceid']}")
            print(f"  AI: 拒绝 | 人工: {r['manual_status']}")
            print(f"  AI备注: {r['remark']}")
            print(f"  人工备注: {r['manual_conclusion']}")
            print(f"  key_conclusions: {r['key_conclusions'][:200] if r.get('key_conclusions') else 'None'}")

            print("\n【P2 样例 2】")
            r = p2_samples[1]
            print(f"  forceid: {r['forceid']}")
            print(f"  AI: 拒绝 | 人工: {r['manual_status']}")
            print(f"  AI备注: {r['remark']}")
            print(f"  人工备注: {r['manual_conclusion']}")
            print(f"  key_conclusions: {r['key_conclusions'][:200] if r.get('key_conclusions') else 'None'}")

            print("\n【P1 样例 1】")
            r = p1_samples[0]
            print(f"  forceid: {r['forceid']}")
            print(f"  AI: 补件 | 人工: {r['manual_status']}")
            print(f"  AI备注: {r['remark']}")
            print(f"  人工备注: {r['manual_conclusion']}")
            print(f"  key_conclusions: {r['key_conclusions'][:200] if r.get('key_conclusions') else 'None'}")

            print("\n【P1 样例 2】")
            r = p1_samples[1]
            print(f"  forceid: {r['forceid']}")
            print(f"  AI: 补件 | 人工: {r['manual_status']}")
            print(f"  AI备注: {r['remark']}")
            print(f"  人工备注: {r['manual_conclusion']}")
            print(f"  key_conclusions: {r['key_conclusions'][:200] if r.get('key_conclusions') else 'None'}")

            # 保存forceid列表供后续脚本使用
            sample_forceids = [r['forceid'] for r in p2_samples + p1_samples]
            print(f"\n样例forceid: {sample_forceids}")

    finally:
        conn.close()

if __name__ == '__main__':
    analyze()
