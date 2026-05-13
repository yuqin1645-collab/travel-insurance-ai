#!/usr/bin/env python3
"""
Phase 1.1: 分析 P2 航班延误「其他」137条案件的 remark 字段
只读查询，不修改任何数据
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql
from collections import defaultdict
from app.config import Config as Settings

def main():
    conn = pymysql.connect(
        host=Settings.DB_HOST,
        port=Settings.DB_PORT,
        user=Settings.DB_USER,
        password=Settings.DB_PASSWORD,
        database=Settings.DB_NAME,
        charset='utf8mb4',
        connect_timeout=15
    )
    cursor = conn.cursor()

    # 查询所有 P2 航班延误案件（AI拒绝/人工通过，排除取消理赔）
    cursor.execute('''
        SELECT forceid, remark, payout_amount, manual_conclusion
        FROM ai_review_result
        WHERE audit_result = '拒绝' AND manual_status = '通过'
          AND claim_type = 'flight_delay'
          AND (manual_conclusion NOT LIKE %s OR manual_conclusion IS NULL)
        ORDER BY remark
    ''', ('%取消理赔%',))

    rows = cursor.fetchall()
    print(f"=== P2 航班延误总计: {len(rows)} 条 ===\n")

    # 按 remark 关键词分类
    categories = defaultdict(list)

    for forceid, remark, payout, manual_note in rows:
        remark_str = (remark or '').strip()
        manual_str = (manual_note or '').strip()

        # 尝试匹配已知类别
        categorized = False

        # 1. 中转接驳免责
        if '中转接驳' in remark_str or '接驳' in remark_str:
            categories['中转接驳免责'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 2. 非客运航班
        if not categorized and ('非客运' in remark_str or '货运' in remark_str or '包机' in remark_str):
            categories['非客运航班'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 3. 同天投保免责
        if not categorized and ('同天投保' in remark_str or '同日投保' in remark_str):
            categories['同天投保免责'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 4. 姓名不符
        if not categorized and ('姓名' in remark_str or '身份' in remark_str or '申请人' in remark_str):
            categories['姓名/身份不匹配'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 5. 承保区域不符
        if not categorized and ('承保区域' in remark_str or '不属承保' in remark_str):
            categories['承保区域不符'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 6. 战争/恐怖活动免责
        if not categorized and ('战争' in remark_str or '战乱' in remark_str or '恐怖' in remark_str):
            categories['战争因素免责'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 7. 未达起赔门槛
        if not categorized and ('未达' in remark_str or '门槛' in remark_str or '不足' in remark_str):
            categories['未达起赔门槛'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 8. 重复索赔
        if not categorized and '重复' in remark_str:
            categories['重复索赔'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 9. 纯国内航班
        if not categorized and '国内' in remark_str:
            categories['纯国内航班'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 10. 超出有效期
        if not categorized and '有效期' in remark_str:
            categories['超出有效期'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 11. 欺诈嫌疑
        if not categorized and ('欺诈' in remark_str or '虚假' in remark_str):
            categories['欺诈嫌疑'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 12. 境内中转免责
        if not categorized and '境内中转' in remark_str:
            categories['境内中转免责'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 13. 必备材料缺失
        if not categorized and ('材料' in remark_str or '缺失' in remark_str or '补件' in remark_str):
            categories['必备材料缺失'].append((forceid, remark_str[:120], payout))
            categorized = True

        # 14. AI模型直接拒赔（remark 以 AI 判断开头或包含特定模式）
        if not categorized and ('【' in remark_str and '】' in remark_str):
            # 提取【xxx】标签
            import re
            tags = re.findall(r'【(.+?)】', remark_str)
            tag_key = ' / '.join(tags[:2])  # 取前2个标签
            categories[f'硬校验标签: {tag_key}'].append((forceid, remark_str[:120], payout))
            categorized = True

        if not categorized:
            # 看 remark 前80个字符来手动分类
            preview = remark_str[:80] if remark_str else '(空)'
            categories[f'未分类: {preview}'].append((forceid, remark_str[:120], payout))

    # 输出分类结果
    print("=" * 80)
    print(f"{'分类':<35} {'数量':<8} {'有赔付':<8}")
    print("-" * 80)

    total_categorized = 0
    for cat_name, items in sorted(categories.items(), key=lambda x: -len(x[1])):
        count = len(items)
        with_payout = sum(1 for _, _, p in items if p and p > 0)
        total_payout = sum(p for _, _, p in items if p and p > 0)
        print(f"{cat_name:<35} {count:<8} {with_payout:<8} (总赔付: {total_payout})")

    print("-" * 80)
    print(f"{'合计':<35} {len(rows):<8}")

    # 输出每个分类的详细列表（前5条样本）
    print("\n\n=== 各类别样本详情 ===\n")
    for cat_name, items in sorted(categories.items(), key=lambda x: -len(x[1])):
        print(f"\n--- {cat_name} ({len(items)}条) ---")
        for forceid, remark, payout in items[:5]:
            print(f"  {forceid}: {remark[:150]}")
            if payout and payout > 0:
                print(f"    人工赔付: {payout}")
        if len(items) > 5:
            print(f"  ... 还有 {len(items)-5} 条")

    # 输出所有 ForceID 列表（方便后续批量重跑）
    print("\n\n=== 所有 P2 航班延误 ForceID 列表 ===\n")
    all_ids = [r[0] for r in rows]
    # 每行输出10个
    for i in range(0, len(all_ids), 10):
        print(' '.join(all_ids[i:i+10]))

    conn.close()

if __name__ == '__main__':
    main()