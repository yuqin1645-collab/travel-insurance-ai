#!/usr/bin/env python3
"""
定时数据库集群分析脚本
每 24 小时自动扫描数据库，更新 issue_cluster_tracker.md

用法:
    python scripts/scheduled_cluster_analysis.py          # 手动运行一次
    python scripts/scheduled_cluster_analysis.py --watch  # 持续运行，每24h执行一次
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql
from app.config import Config as Settings


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=Settings.DB_HOST,
        port=Settings.DB_PORT,
        user=Settings.DB_USER,
        password=Settings.DB_PASSWORD,
        database=Settings.DB_NAME,
        charset='utf8mb4',
        connect_timeout=15
    )


def run_full_analysis():
    """执行全量数据库分析，返回分析结果字典"""
    conn = get_db_connection()
    cursor = conn.cursor()
    results = {}
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    # 1. 总记录数
    cursor.execute("SELECT COUNT(*) FROM ai_review_result")
    results['total_records'] = cursor.fetchone()[0]

    # 2. 交叉统计（排除取消理赔）
    cursor.execute('''
        SELECT audit_result, manual_status, COUNT(*) as cnt
        FROM ai_review_result
        WHERE audit_result IS NOT NULL AND manual_status IS NOT NULL
          AND (manual_conclusion NOT LIKE %s OR manual_conclusion IS NULL)
        GROUP BY audit_result, manual_status
        ORDER BY cnt DESC
    ''', ('%取消理赔%',))
    cross_tab = []
    for row in cursor.fetchall():
        cross_tab.append({'ai': row[0], 'manual': row[1], 'count': row[2]})
    results['cross_tab'] = cross_tab

    # 3. 计算一致率
    consistent = sum(r['count'] for r in cross_tab if r['ai'] == r['manual'])
    total_with_both = sum(r['count'] for r in cross_tab)
    results['consistent_count'] = consistent
    results['total_with_both'] = total_with_both
    results['consistency_rate'] = round(consistent / total_with_both * 100, 1) if total_with_both > 0 else 0

    # 4. P0: AI通过但人工拒绝（排除取消理赔）
    cursor.execute('''
        SELECT forceid, claim_type, audit_result, manual_status, manual_conclusion, remark, payout_amount
        FROM ai_review_result
        WHERE audit_result = %s AND manual_status = %s
          AND (manual_conclusion NOT LIKE %s OR manual_conclusion IS NULL)
    ''', ('通过', '拒绝', '%取消理赔%'))
    results['p0'] = []
    for row in cursor.fetchall():
        results['p0'].append({
            'forceid': row[0], 'claim_type': row[1], 'ai': row[2], 'manual': row[3],
            'manual_note': row[4], 'remark': row[5][:200] if row[5] else None, 'payout': float(row[6]) if row[6] else 0
        })

    # 5. P1: AI补件但人工通过/拒绝（排除取消理赔）
    cursor.execute('''
        SELECT forceid, claim_type, audit_result, manual_status, manual_conclusion, remark, payout_amount
        FROM ai_review_result
        WHERE audit_result = %s AND manual_status IN (%s, %s)
          AND (manual_conclusion NOT LIKE %s OR manual_conclusion IS NULL)
        ORDER BY manual_status, claim_type
    ''', ('需补齐资料', '通过', '拒绝', '%取消理赔%'))
    results['p1'] = []
    for row in cursor.fetchall():
        results['p1'].append({
            'forceid': row[0], 'claim_type': row[1], 'ai': row[2], 'manual': row[3],
            'manual_note': row[4], 'remark': row[5][:200] if row[5] else None, 'payout': float(row[6]) if row[6] else 0
        })

    # 6. P2: AI拒绝但人工通过（排除取消理赔）
    cursor.execute('''
        SELECT forceid, claim_type, audit_result, manual_status, manual_conclusion, remark, payout_amount
        FROM ai_review_result
        WHERE audit_result = %s AND manual_status = %s
          AND (manual_conclusion NOT LIKE %s OR manual_conclusion IS NULL)
    ''', ('拒绝', '通过', '%取消理赔%'))
    results['p2'] = []
    for row in cursor.fetchall():
        results['p2'].append({
            'forceid': row[0], 'claim_type': row[1], 'ai': row[2], 'manual': row[3],
            'manual_note': row[4], 'remark': row[5][:200] if row[5] else None, 'payout': float(row[6]) if row[6] else 0
        })

    # 7. 按险种分类
    for prefix, key in [('p0', 'p0'), ('p1', 'p1'), ('p2', 'p2')]:
        flight = [r for r in results[key] if r['claim_type'] == 'flight_delay']
        baggage = [r for r in results[key] if r['claim_type'] == 'baggage_delay']
        results[f'{key}_flight_count'] = len(flight)
        results[f'{key}_baggage_count'] = len(baggage)

    # 8. P2 拒赔原因分类（航班延误）
    reason_map = {
        '延误0分钟': ('未达起赔门槛', '0分钟'),
        '延误>0但不足门槛': ('未达起赔门槛',),
        '重复索赔': ('重复',),
        '战争因素': ('战争', '战乱'),
        '纯国内航班': ('国内',),
        '承保区域不符': ('承保区域', '不属承保'),
        '身份不匹配': ('姓名', '身份'),
        '超出有效期': ('有效期',),
        '中转接驳免责': ('中转接驳',),
        '同天投保免责': ('同天投保', '同日投保'),
        '境内中转免责': ('境内中转',),
        '欺诈嫌疑': ('欺诈', '虚假'),
        '必备材料缺失': ('材料', '缺失', '补件'),
    }
    p2_flight_reasons = defaultdict(lambda: {'count': 0, 'total_payout': 0.0})
    for r in results['p2']:
        if r['claim_type'] != 'flight_delay':
            continue
        remark = r['remark'] or ''
        matched = False
        for reason_name, keywords in reason_map.items():
            if all(kw in remark for kw in keywords):
                p2_flight_reasons[reason_name]['count'] += 1
                p2_flight_reasons[reason_name]['total_payout'] += (r['payout'] or 0)
                matched = True
                break
        if not matched:
            p2_flight_reasons['其他']['count'] += 1
            p2_flight_reasons['其他']['total_payout'] += (r['payout'] or 0)
    results['p2_flight_reasons'] = dict(p2_flight_reasons)

    # 9. P2 行李延误拒赔原因分类
    p2_baggage_reasons = defaultdict(int)
    for r in results['p2']:
        if r['claim_type'] != 'baggage_delay':
            continue
        remark = r['remark'] or ''
        if '未达' in remark and '6' in remark:
            p2_baggage_reasons['延误未达6h门槛'] += 1
        elif '丢失' in remark:
            p2_baggage_reasons['事故类型为行李丢失'] += 1
        elif '有效期' in remark:
            p2_baggage_reasons['超出保单有效期'] += 1
        elif '国内' in remark:
            p2_baggage_reasons['纯国内航班'] += 1
        else:
            p2_baggage_reasons['其他'] += 1
    results['p2_baggage_reasons'] = dict(p2_baggage_reasons)

    # 10. P1 补件原因关键词统计
    p1_flight_keywords = defaultdict(int)
    p1_baggage_keywords = defaultdict(int)
    for r in results['p1']:
        remark = r['remark'] or ''
        if r['claim_type'] == 'flight_delay':
            if '出入境' in remark:
                p1_flight_keywords['出入境记录'] += 1
            if '登机牌' in remark or '行程单' in remark or '客票' in remark:
                p1_flight_keywords['登机牌/行程单'] += 1
            if '延误证明' in remark or '航班变动' in remark or '不正常' in remark:
                p1_flight_keywords['延误证明/航班变动证明'] += 1
            if '护照' in remark:
                p1_flight_keywords['护照照片页'] += 1
            if '监护' in remark or '未成年' in remark:
                p1_flight_keywords['监护人材料'] += 1
            if '改签' in remark:
                p1_flight_keywords['改签相关'] += 1
        else:
            if '签收' in remark:
                p1_baggage_keywords['行李签收证明'] += 1
            if '交通' in remark or '票据' in remark:
                p1_baggage_keywords['交通票据'] += 1
            if '延误证明' in remark or '延误时间' in remark:
                p1_baggage_keywords['行李延误证明'] += 1
    results['p1_flight_keywords'] = dict(p1_flight_keywords)
    results['p1_baggage_keywords'] = dict(p1_baggage_keywords)

    # 11. 待定/NULL统计
    cursor.execute('''
        SELECT manual_status, COUNT(*) FROM ai_review_result
        WHERE manual_status = '待定' OR audit_result IS NULL
        GROUP BY manual_status
    ''')
    pending_stats = {}
    for row in cursor.fetchall():
        pending_stats[row[0] or 'NULL'] = row[1]
    results['pending_stats'] = pending_stats

    # 12. 最近14天趋势（排除取消理赔）
    cursor.execute('''
        SELECT DATE(created_at) as dt, COUNT(*) as cnt,
               SUM(CASE WHEN audit_result = manual_status THEN 1 ELSE 0 END) as consistent
        FROM ai_review_result
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 14 DAY)
          AND (manual_conclusion NOT LIKE %s OR manual_conclusion IS NULL)
        GROUP BY DATE(created_at)
        ORDER BY dt DESC
    ''', ('%取消理赔%',))
    daily_trend = []
    for row in cursor.fetchall():
        daily_trend.append({'date': str(row[0]), 'total': row[1], 'consistent': row[2]})
    results['daily_trend'] = daily_trend

    # 13. P2 有实际赔付的案件（实质性差异）
    p2_with_payout = [r for r in results['p2'] if r['payout'] and r['payout'] > 0]
    results['p2_with_payout'] = p2_with_payout

    conn.close()
    results['analysis_time'] = now
    return results


def generate_markdown_report(results):
    """根据分析结果生成 Markdown 报告"""
    now = results['analysis_time']
    total = results['total_records']
    consistent = results['consistent_count']
    total_both = results['total_with_both']
    rate = results['consistency_rate']

    p0 = results['p0']
    p1 = results['p1']
    p2 = results['p2']

    p0_flight = results['p0_flight_count']
    p0_baggage = results['p0_baggage_count']
    p1_flight = results['p1_flight_count']
    p1_baggage = results['p1_baggage_count']
    p2_flight = results['p2_flight_count']
    p2_baggage = results['p2_baggage_count']

    # P1 按人工状态分
    p1_pass = [r for r in p1 if r['manual'] == '通过']
    p1_reject = [r for r in p1 if r['manual'] == '拒绝']
    p1_pass_flight = [r for r in p1_pass if r['claim_type'] == 'flight_delay']
    p1_pass_baggage = [r for r in p1_pass if r['claim_type'] == 'baggage_delay']

    lines = []
    lines.append("# AI vs 人工审核差异聚类追踪报告")
    lines.append("")
    lines.append(f"> 分析日期: {now[:10]}")
    lines.append("> 数据源: 数据库 `ai_review_result` 表（阿里云 RDS），**数据库驱动分析**")
    lines.append("> 对比维度: AI `audit_result` vs 人工 `manual_status`（标准化字段）")
    lines.append("> 说明: `manual_conclusion` 为人工备注自由文本，`manual_status` 为标准化状态值")
    lines.append(f"> 自动扫描时间: {now}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. 总体概况 — Database Snapshot")
    lines.append("")
    lines.append("| 指标 | 数值 | 数据来源 |")
    lines.append("|------|------|---------|")
    lines.append(f"| 数据库总记录数 | **{total}** | `SELECT COUNT(*) FROM ai_review_result` |")
    lines.append(f"| 同时有 AI 和人工状态的记录 | **{total_both}** | 排除待定/NULL |")
    lines.append(f"| AI = 人工（一致） | **{consistent}** ({rate}%) | audit_result ≈ manual_status |")
    lines.append(f"| AI ≠ 人工（不一致） | **{total_both - consistent}** ({round(100-rate,1)}%) | — |")
    lines.append("")
    lines.append("### 分类统计")
    lines.append("")
    lines.append("| 分类 | 数量 | 航班延误 | 行李延误 | 定义 |")
    lines.append("|------|------|---------|---------|------|")
    lines.append(f"| 一致 | {consistent} | — | — | AI结论与人工状态一致 |")
    lines.append(f"| **P0**: AI通过但人工拒绝 | **{len(p0)}** | {p0_flight} | {p0_baggage} | 最严重：AI漏审拒赔因素 |")
    lines.append(f"| **P1**: AI补件但人工通过/拒绝 | **{len(p1)}** | {p1_flight} | {p1_baggage} | AI材料门禁过严 |")
    lines.append(f"| **P2**: AI拒绝但人工通过 | **{len(p2)}** | {p2_flight} | {p2_baggage} | AI拒赔标准偏严 |")
    lines.append("")
    lines.append("### AI vs 人工 完整交叉表")
    lines.append("")
    lines.append("| AI结论 | 人工状态 | 数量 |")
    lines.append("|--------|---------|------|")
    for r in results['cross_tab']:
        lines.append(f"| {r['ai']} | {r['manual']} | {r['count']} |")
    lines.append("")

    # P0 部分
    lines.append("---")
    lines.append("")
    p0_icon = "!!" if len(p0) > 0 else "OK"
    lines.append(f"## 2. P0 — AI通过但人工拒绝（最严重）{p0_icon}")
    lines.append("")
    lines.append(f"**数量: {len(p0)} 件** {p0_icon}")
    lines.append(f"**数据库验证: {now[:10]}**")
    lines.append(f"**DB Sample Count: {len(p0)}**")
    trend_icon = "↑" if len(p0) > 0 else "→"
    lines.append(f"**DB Trend: {trend_icon}**")
    lines.append(f"**Last DB Validation: {now[:10]}**")
    lines.append("")

    if len(p0) > 0:
        lines.append("### ⚠️ P0 案件详情")
        lines.append("")
        lines.append("| forceid | 险种 | AI | 人工 | 人工备注摘要 | AI赔付 |")
        lines.append("|---------|------|-----|------|-------------|--------|")
        for r in p0:
            note = (r['manual_note'] or '')[:60]
            lines.append(f"| {r['forceid']} | {r['claim_type']} | {r['ai']} | {r['manual']} | {note} | {r['payout']} |")
        lines.append("")
        lines.append("**处理建议**: 需立即用最新代码重跑并推送数据库。")
    else:
        lines.append("**P0 已清零。** ✅")
    lines.append("")

    # P1 部分
    lines.append("---")
    lines.append("")
    lines.append("## 3. P1 — AI补件但人工通过/拒绝")
    lines.append("")
    lines.append(f"**数量: {len(p1)} 件（航班延误 {p1_flight}，行李延误 {p1_baggage}）**")
    lines.append(f"**数据库验证: {now[:10]}**")
    lines.append(f"**DB Sample Count: {len(p1)}**")
    lines.append(f"**Last DB Validation: {now[:10]}**")
    lines.append("")

    # P1 航班延误
    lines.append(f"### 3.1 航班延误（{p1_flight} 件）")
    lines.append("")
    p1_flight_pass = len([r for r in p1_pass if r['claim_type'] == 'flight_delay'])
    p1_flight_reject = len([r for r in p1_reject if r['claim_type'] == 'flight_delay'])
    lines.append(f"| 人工状态 | 数量 |")
    lines.append("|---------|------|")
    lines.append(f"| 通过 | {p1_flight_pass} |")
    lines.append(f"| 拒绝 | {p1_flight_reject} |")
    lines.append("")
    if results['p1_flight_keywords']:
        lines.append("**补件原因关键词：**")
        lines.append("")
        for kw, cnt in sorted(results['p1_flight_keywords'].items(), key=lambda x: -x[1]):
            lines.append(f"- {kw}: {cnt}件")
        lines.append("")

    # P1 行李延误
    lines.append(f"### 3.2 行李延误（{p1_baggage} 件）")
    lines.append("")
    p1_baggage_pass = len([r for r in p1_pass if r['claim_type'] == 'baggage_delay'])
    lines.append(f"| 人工状态 | 数量 |")
    lines.append("|---------|------|")
    lines.append(f"| 通过 | {p1_baggage_pass} |")
    lines.append("")
    if results['p1_baggage_keywords']:
        lines.append("**补件原因关键词：**")
        lines.append("")
        for kw, cnt in sorted(results['p1_baggage_keywords'].items(), key=lambda x: -x[1]):
            lines.append(f"- {kw}: {cnt}件")
        lines.append("")

    # P2 部分
    lines.append("---")
    lines.append("")
    lines.append("## 4. P2 — AI拒绝但人工通过")
    lines.append("")
    lines.append(f"**数量: {len(p2)} 件（航班延误 {p2_flight}，行李延误 {p2_baggage}）**")
    lines.append(f"**数据库验证: {now[:10]}**")
    lines.append(f"**DB Sample Count: {len(p2)}**")
    lines.append(f"**Last DB Validation: {now[:10]}**")
    lines.append("")

    # P2 航班延误
    lines.append(f"### 4.1 航班延误（{p2_flight} 件）")
    lines.append("")
    if results['p2_flight_reasons']:
        lines.append("**拒赔原因分布：**")
        lines.append("")
        lines.append("| 拒赔原因 | 数量 | 总赔付 |")
        lines.append("|---------|------|--------|")
        for reason, data in sorted(results['p2_flight_reasons'].items(), key=lambda x: -x[1]['count']):
            lines.append(f"| {reason} | {data['count']} | {data['total_payout']} |")
        lines.append("")

    # P2 有赔付
    if results['p2_with_payout']:
        lines.append(f"**人工实际有赔付（{len(results['p2_with_payout'])}件）⚠️ 实质性差异：**")
        lines.append("")
        lines.append("| forceid | AI拒赔原因 | 人工赔付 |")
        lines.append("|---------|-----------|---------|")
        for r in results['p2_with_payout']:
            remark_short = (r['remark'] or '')[:60]
            lines.append(f"| {r['forceid']} | {remark_short} | {r['payout']} |")
        lines.append("")

    # P2 行李延误
    lines.append(f"### 4.2 行李延误（{p2_baggage} 件）")
    lines.append("")
    if results['p2_baggage_reasons']:
        lines.append("**拒赔原因分布：**")
        lines.append("")
        lines.append("| 拒赔原因 | 数量 |")
        lines.append("|---------|------|")
        for reason, cnt in sorted(results['p2_baggage_reasons'].items(), key=lambda x: -x[1]):
            lines.append(f"| {reason} | {cnt} |")
        lines.append("")

    # 趋势
    lines.append("---")
    lines.append("")
    lines.append("## 5. 最近14天趋势")
    lines.append("")
    if results['daily_trend']:
        lines.append("| 日期 | 新增 | 一致 | 一致率 |")
        lines.append("|------|------|------|--------|")
        for d in results['daily_trend']:
            r = round(d['consistent'] / d['total'] * 100, 1) if d['total'] > 0 else 0
            lines.append(f"| {d['date']} | {d['total']} | {d['consistent']} | {r}% |")
        lines.append("")

    # 待定
    if results['pending_stats']:
        lines.append("---")
        lines.append("")
        lines.append("## 6. 数据质量")
        lines.append("")
        for status, cnt in results['pending_stats'].items():
            lines.append(f"- {status}: {cnt}件")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*本报告由定时脚本 `scripts/scheduled_cluster_analysis.py` 自动生成。*")
    lines.append(f"*数据源: 数据库 `ai_review_result` 表全量数据。*")
    lines.append(f"*自动生成时间: {now}*")

    return '\n'.join(lines)


def save_report(markdown_content):
    """保存报告到 docs/issue_cluster_tracker.md"""
    report_path = Path(__file__).resolve().parent.parent / 'docs' / 'issue_cluster_tracker.md'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 报告已保存到: {report_path}")


def save_results_json(results):
    """保存原始分析数据到 JSON 文件"""
    json_path = Path(__file__).resolve().parent.parent / 'docs' / 'cluster_analysis_data.json'
    # 转换不可序列化的对象
    serializable = {}
    for k, v in results.items():
        if isinstance(v, list):
            serializable[k] = v
        elif isinstance(v, dict):
            serializable[k] = v
        elif isinstance(v, (int, float, str)):
            serializable[k] = v
        else:
            serializable[k] = str(v)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, default=str)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 原始数据已保存到: {json_path}")


def analyze_and_report():
    """执行分析并生成报告"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始数据库扫描分析...")
    try:
        results = run_full_analysis()
        print(f"  总记录: {results['total_records']}")
        print(f"  一致率: {results['consistency_rate']}%")
        print(f"  P0: {len(results['p0'])}件, P1: {len(results['p1'])}件, P2: {len(results['p2'])}件")

        markdown = generate_markdown_report(results)
        save_report(markdown)
        save_results_json(results)

        # 输出 P0 警告
        if len(results['p0']) > 0:
            print(f"\n  !! P0警告: 发现 {len(results['p0'])} 件P0案件! 需要立即处理!")
            for r in results['p0']:
                print(f"    - {r['forceid']} ({r['claim_type']})")

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 分析完成。")
        return results
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 分析失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def watch_mode(interval_hours=24):
    """持续监控模式，每隔指定小时执行一次"""
    interval_seconds = interval_hours * 3600
    print(f"进入监控模式，每 {interval_hours} 小时执行一次分析...")
    print(f"按 Ctrl+C 停止。")
    print()

    while True:
        analyze_and_report()
        next_run = datetime.now() + timedelta(seconds=interval_seconds)
        print(f"\n下次执行时间: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"等待 {interval_hours} 小时...\n")
        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n监控模式已停止。")
            break


def main():
    parser = argparse.ArgumentParser(description='定时数据库集群分析脚本')
    parser.add_argument('--watch', action='store_true', help='持续监控模式')
    parser.add_argument('--interval', type=float, default=24.0, help='监控间隔（小时），默认24')
    parser.add_argument('--json-only', action='store_true', help='只保存JSON数据，不更新md报告')
    args = parser.parse_args()

    if args.watch:
        watch_mode(args.interval)
    elif args.json_only:
        results = run_full_analysis()
        save_results_json(results)
    else:
        analyze_and_report()


if __name__ == '__main__':
    main()
