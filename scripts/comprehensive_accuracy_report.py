#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""综合准确率报告 — 生成 Excel 文件"""
import os
import sys
import pymysql
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ── 样式 ──
HEADER_FONT = Font(name='Arial', bold=True, color='FFFFFF', size=11)
TITLE_FONT = Font(name='Arial', bold=True, color='1F4E79', size=14)
SUBTITLE_FONT = Font(name='Arial', bold=True, color='1F4E79', size=11)
DATA_FONT = Font(name='Arial', size=10)
CONSISTENT_FONT = Font(name='Arial', color='006600', size=10)
INCONSISTENT_FONT = Font(name='Arial', color='CC0000', size=10)

HEADER_FILL = PatternFill('solid', fgColor='1F4E79')
CONSISTENT_FILL = PatternFill('solid', fgColor='E8F5E9')
INCONSISTENT_FILL = PatternFill('solid', fgColor='FFEBEE')
ALT_FILL = PatternFill('solid', fgColor='F2F7FB')
TITLE_FILL = PatternFill('solid', fgColor='D6E4F0')

THIN_BORDER = Border(
    left=Side(style='thin', color='B0B0B0'),
    right=Side(style='thin', color='B0B0B0'),
    top=Side(style='thin', color='B0B0B0'),
    bottom=Side(style='thin', color='B0B0B0'),
)

PERCENT_FMT = '0.0%'
DATE_FMT = 'YYYY-MM-DD HH:MM'


def get_conn():
    return pymysql.connect(
        host="rds3335l2v6qar8zqontg.mysql.rds.aliyuncs.com",
        port=3306,
        user="aiuser",
        password="AI@ssish",
        database="ai",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_all_cases(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                forceid, benefit_name, claim_type,
                audit_result, audit_status, audit_time,
                manual_status, manual_conclusion,
                confidence_score, payout_amount,
                identity_match, threshold_met, exclusion_triggered,
                first_ai_audit_time, first_ai_manual_status,
                created_at, updated_at
            FROM ai_review_result
            WHERE created_at >= '2026-05-15 17:00:00'
            ORDER BY created_at
        """)
        return cur.fetchall()


def fetch_accuracy_stats(conn):
    stats = {}
    # 全量统计（排除待定/空值）
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN audit_result = manual_status THEN 1 ELSE 0 END) as consistent,
                SUM(CASE WHEN audit_result != manual_status THEN 1 ELSE 0 END) as inconsistent
            FROM ai_review_result
            WHERE created_at >= '2026-05-15 17:00:00'
              AND manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None' AND manual_status != '待定'
              AND audit_result IS NOT NULL AND audit_result != ''
        """)
        row = cur.fetchone()
        stats['total'] = row['total']
        stats['consistent'] = row['consistent']
        stats['inconsistent'] = row['inconsistent']
        stats['accuracy'] = row['consistent'] / row['total'] if row['total'] > 0 else 0

    # 按险种
    with conn.cursor() as cur:
        cur.execute("""
            SELECT benefit_name,
                COUNT(*) as total,
                SUM(CASE WHEN audit_result = manual_status THEN 1 ELSE 0 END) as consistent
            FROM ai_review_result
            WHERE created_at >= '2026-05-15 17:00:00'
              AND manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None' AND manual_status != '待定'
              AND audit_result IS NOT NULL AND audit_result != ''
            GROUP BY benefit_name
        """)
        stats['by_type'] = cur.fetchall()

    # 交叉表
    with conn.cursor() as cur:
        cur.execute("""
            SELECT audit_result,
                SUM(CASE WHEN manual_status = '通过' THEN 1 ELSE 0 END) as p_pass,
                SUM(CASE WHEN manual_status = '拒绝' THEN 1 ELSE 0 END) as p_reject,
                SUM(CASE WHEN manual_status = '需补齐资料' THEN 1 ELSE 0 END) as p_supplement,
                COUNT(*) as total
            FROM ai_review_result
            WHERE created_at >= '2026-05-15 17:00:00'
              AND manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None'
              AND audit_result IS NOT NULL AND audit_result != ''
            GROUP BY audit_result
            ORDER BY audit_result
        """)
        stats['cross_table'] = cur.fetchall()

    # 不一致明细
    with conn.cursor() as cur:
        cur.execute("""
            SELECT audit_result, manual_status, COUNT(*) as cnt
            FROM ai_review_result
            WHERE created_at >= '2026-05-15 17:00:00'
              AND audit_result != manual_status
              AND manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None' AND manual_status != '待定'
              AND audit_result IS NOT NULL AND audit_result != ''
            GROUP BY audit_result, manual_status
            ORDER BY cnt DESC
        """)
        stats['inconsistency'] = cur.fetchall()

    # 按天趋势
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DATE(created_at) as day,
                COUNT(*) as total,
                SUM(CASE WHEN audit_result = manual_status AND manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None' AND manual_status != '待定' AND audit_result IS NOT NULL AND audit_result != '' THEN 1 ELSE 0 END) as consistent
            FROM ai_review_result
            WHERE created_at >= '2026-05-15 17:00:00'
              AND manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None'
              AND audit_result IS NOT NULL AND audit_result != ''
            GROUP BY DATE(created_at)
            ORDER BY day
        """)
        stats['trend'] = cur.fetchall()

    # 全量累计（对比基准）
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN audit_result = manual_status THEN 1 ELSE 0 END) as consistent
            FROM ai_review_result
            WHERE manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None' AND manual_status != '待定'
              AND audit_result IS NOT NULL AND audit_result != ''
        """)
        row = cur.fetchone()
        stats['grand_total'] = row['total']
        stats['grand_consistent'] = row['consistent']
        stats['grand_accuracy'] = row['consistent'] / row['total'] if row['total'] > 0 else 0

    # 按险种累计
    with conn.cursor() as cur:
        cur.execute("""
            SELECT benefit_name,
                COUNT(*) as total,
                SUM(CASE WHEN audit_result = manual_status THEN 1 ELSE 0 END) as consistent
            FROM ai_review_result
            WHERE manual_status IS NOT NULL AND manual_status != '' AND manual_status != 'None' AND manual_status != '待定'
              AND audit_result IS NOT NULL AND audit_result != ''
            GROUP BY benefit_name
        """)
        stats['grand_by_type'] = cur.fetchall()

    return stats


def write_cell(ws, row, col, value, font=DATA_FONT, fill=None, alignment=None, number_format=None, border=THIN_BORDER):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font
    cell.fill = fill if fill else PatternFill()
    cell.alignment = alignment or Alignment(horizontal='left', vertical='center')
    if number_format:
        cell.number_format = number_format
    if border:
        cell.border = border
    return cell


def write_title_row(ws, row, text, max_col=6):
    write_cell(ws, row, 1, text, font=TITLE_FONT, fill=TITLE_FILL,
               alignment=Alignment(horizontal='left', vertical='center'))
    for c in range(2, max_col + 1):
        write_cell(ws, row, c, '', fill=TITLE_FILL)


def write_header_row(ws, row, headers):
    for c, h in enumerate(headers, 1):
        write_cell(ws, row, c, h, font=HEADER_FONT, fill=HEADER_FILL,
                   alignment=Alignment(horizontal='center', vertical='center'))


def main():
    conn = get_conn()
    cases = fetch_all_cases(conn)
    stats = fetch_accuracy_stats(conn)
    conn.close()

    wb = Workbook()

    # ═══════════════════════════════════════════════
    # Sheet 1: 案件总览
    # ═══════════════════════════════════════════════
    ws1 = wb.active
    ws1.title = "案件总览"
    ws1.sheet_properties.tabColor = '1F4E79'

    write_title_row(ws1, 1, "AI审核案件总览（2026-05-15 至今）")
    ws1.merge_cells('A1:N1')

    write_title_row(ws1, 2, f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  共 {len(cases)} 件案件")
    ws1.merge_cells('A2:N2')

    headers = [
        "序号", "ForceID", "险种", "ClaimType",
        "AI结论", "人工状态", "是否一致", "赔付金额",
        "置信度", "身份匹配", "门槛满足", "除外触发",
        "创建时间", "更新时间"
    ]
    write_header_row(ws1, 4, headers)

    for i, case in enumerate(cases, 1):
        r = i + 4
        consistent = (case['audit_result'] == case['manual_status']) if case.get('manual_status') and case.get('audit_result') else None
        if consistent is None:
            consistent_label = "—"
        elif consistent:
            consistent_label = "一致"
        else:
            consistent_label = "不一致"

        fill = CONSISTENT_FILL if consistent else (INCONSISTENT_FILL if consistent is False else None)
        fnt = CONSISTENT_FONT if consistent else (INCONSISTENT_FONT if consistent is False else DATA_FONT)

        vals = [
            i, case['forceid'], case['benefit_name'] or '—', case['claim_type'] or '—',
            case['audit_result'] or '—', case['manual_status'] or '—',
            consistent_label, case['payout_amount'] or '—',
            case['confidence_score'] or '—',
            case['identity_match'] or '—',
            case['threshold_met'] or '—',
            case['exclusion_triggered'] or '—',
            case['created_at'], case['updated_at']
        ]
        for c, v in enumerate(vals, 1):
            write_cell(ws1, r, c, v, font=fnt if c == 7 else DATA_FONT, fill=fill if c == 7 else None,
                       alignment=Alignment(horizontal='center', vertical='center') if c <= 12 else Alignment(horizontal='left', vertical='center'))

    # 列宽
    from openpyxl.utils import get_column_letter
    widths = [6, 18, 10, 14, 12, 12, 10, 10, 10, 10, 10, 10, 18, 18]
    for c, w in enumerate(widths, 1):
        ws1.column_dimensions[get_column_letter(c)].width = w

    ws1.freeze_panes = 'A5'

    # ═══════════════════════════════════════════════
    # Sheet 2: 准确率统计
    # ═══════════════════════════════════════════════
    ws2 = wb.create_sheet("准确率统计")
    ws2.sheet_properties.tabColor = '2ECC71'

    # 总体概况
    write_title_row(ws2, 1, "总体准确率统计", max_col=4)
    ws2.merge_cells('A1:D1')

    overview = [
        ["指标", "数值", "", ""],
        ["5月15日后新增案件总数", stats['total'], "", ""],
        ["有人工状态的案件", stats['total'], "", ""],
        ["AI = 人工（一致）", stats['consistent'], "", ""],
        ["AI ≠ 人工（不一致）", stats['inconsistent'], "", ""],
        ["综合准确率", stats['accuracy'], "", ""],
        ["", "", "", ""],
        ["全量累计（对比基准）", "", "", ""],
        ["数据库总案件数", stats['grand_total'], "", ""],
        ["全量综合准确率", stats['grand_accuracy'], "", ""],
    ]
    for i, row_data in enumerate(overview, 3):
        for c, v in enumerate(row_data, 1):
            if i == 7 and c == 1:
                write_cell(ws2, i, c, v, font=SUBTITLE_FONT, fill=TITLE_FILL)
            elif i in [8, 9] and c == 1:
                write_cell(ws2, i, c, v, font=Font(name='Arial', bold=True, size=10))
            elif i in (7, 8, 9) and c == 2:
                write_cell(ws2, i, c, v, font=Font(name='Arial', size=10))
            elif c == 1:
                write_cell(ws2, i, c, v, font=Font(name='Arial', bold=True, size=10))
            elif c == 2 and i == 7:
                write_cell(ws2, i, c, v, font=Font(name='Arial', bold=True, size=11, color='1F4E79'),
                           number_format=PERCENT_FMT)
            elif c == 2 and i in (5, 6):
                write_cell(ws2, i, c, v, font=Font(name='Arial', bold=True, size=10, color='CC0000' if i == 6 else '006600'))
            else:
                write_cell(ws2, i, c, v, font=DATA_FONT)

    # 按险种统计
    r = 14
    write_title_row(ws2, r, "按险种分类准确率", max_col=6)
    ws2.merge_cells(f'A{r}:F{r}')
    r += 1
    write_header_row(ws2, r, ["险种", "案件数", "一致数", "不一致数", "准确率", "累计准确率"])
    r += 1
    for by_type in stats['by_type']:
        bn = by_type['benefit_name'] or '未知'
        total = by_type['total']
        cons = by_type['consistent']
        incons = total - cons
        acc = cons / total if total > 0 else 0
        # 找累计值
        grand_acc = None
        for gt in stats['grand_by_type']:
            if gt['benefit_name'] == bn:
                grand_acc = gt['consistent'] / gt['total'] if gt['total'] > 0 else 0
                break

        vals = [bn, total, cons, incons, acc, grand_acc]
        for c, v in enumerate(vals, 1):
            write_cell(ws2, r, c, v,
                       font=Font(name='Arial', bold=True, size=10) if c == 5 else DATA_FONT,
                       number_format=PERCENT_FMT if c in (5, 6) else None,
                       fill=ALT_FILL if r % 2 == 0 else None)
        r += 1

    # 交叉表
    r += 1
    write_title_row(ws2, r, "AI vs 人工交叉表", max_col=5)
    ws2.merge_cells(f'A{r}:E{r}')
    r += 1
    write_header_row(ws2, r, ["AI结论", "人工=通过", "人工=拒绝", "人工=需补齐资料", "合计"])
    r += 1
    for row in stats['cross_table']:
        vals = [row['audit_result'], row['p_pass'], row['p_reject'], row['p_supplement'], row['total']]
        for c, v in enumerate(vals, 1):
            write_cell(ws2, r, c, v,
                       font=Font(name='Arial', bold=True, size=10) if c == 5 else DATA_FONT,
                       fill=ALT_FILL if r % 2 == 0 else None)
        r += 1

    # 不一致原因分布
    r += 1
    write_title_row(ws2, r, "不一致原因分布", max_col=4)
    ws2.merge_cells(f'A{r}:D{r}')
    r += 1
    write_header_row(ws2, r, ["AI结论", "人工状态", "案件数", "占比"])
    r += 1
    for row in stats['inconsistency']:
        pct = row['cnt'] / stats['inconsistent'] if stats['inconsistent'] > 0 else 0
        vals = [row['audit_result'], row['manual_status'], row['cnt'], pct]
        for c, v in enumerate(vals, 1):
            write_cell(ws2, r, c, v, font=INCONSISTENT_FONT if c <= 3 else DATA_FONT,
                       number_format=PERCENT_FMT if c == 4 else None,
                       fill=INCONSISTENT_FILL if c <= 3 else None)
        r += 1

    ws2.column_dimensions['A'].width = 20
    ws2.column_dimensions['B'].width = 15
    ws2.column_dimensions['C'].width = 12
    ws2.column_dimensions['D'].width = 15
    ws2.column_dimensions['E'].width = 15
    ws2.column_dimensions['F'].width = 15
    ws2.freeze_panes = 'A2'

    # ═══════════════════════════════════════════════
    # Sheet 3: 趋势分析
    # ═══════════════════════════════════════════════
    ws3 = wb.create_sheet("趋势分析")
    ws3.sheet_properties.tabColor = 'E67E22'

    write_title_row(ws3, 1, "每日处理量与准确率趋势", max_col=5)
    ws3.merge_cells('A1:E1')

    write_header_row(ws3, 3, ["日期", "新增案件数", "一致数", "不一致数", "当日准确率"])
    for i, trend in enumerate(stats['trend'], 1):
        r = i + 3
        total = trend['total']
        cons = trend['consistent']
        incons = total - cons
        acc = cons / total if total > 0 else 0
        vals = [trend['day'], total, cons, incons, acc]
        for c, v in enumerate(vals, 1):
            write_cell(ws3, r, c, v,
                       font=Font(name='Arial', bold=True, size=10) if c == 5 else DATA_FONT,
                       number_format=DATE_FMT if c == 1 else (PERCENT_FMT if c == 5 else None),
                       alignment=Alignment(horizontal='center', vertical='center'))

    ws3.column_dimensions['A'].width = 15
    ws3.column_dimensions['B'].width = 15
    ws3.column_dimensions['C'].width = 12
    ws3.column_dimensions['D'].width = 15
    ws3.column_dimensions['E'].width = 15
    ws3.freeze_panes = 'A4'

    # ═══════════════════════════════════════════════
    # 保存
    # ═══════════════════════════════════════════════
    output_path = ROOT / "output" / f"AI审核准确率报告_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    print(f"报告已生成: {output_path}")


if __name__ == '__main__':
    main()
