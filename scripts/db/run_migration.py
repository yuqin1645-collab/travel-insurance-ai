#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
迁移执行脚本 - 按顺序执行 007 → 008 → 验证 → 010 → 009
"""
import os
import re
import sys
import pymysql
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS = {
    '007': 'scripts/db/migrations/007_create_subtables_and_add_columns.sql',
    '008': 'scripts/db/migrations/008_migrate_data_to_subtables.sql',
    '009': 'scripts/db/migrations/009_drop_migrated_columns.sql',
    '010': 'scripts/db/migrations/010_update_views.sql',
}


def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
    )


def run_sql_file(conn, path, name):
    print(f"\n{'='*60}")
    print(f" >>> 执行: {name}")
    print(f"{'='*60}")
    with open(path, 'r', encoding='utf-8') as f:
        sql_text = f.read()

    # 拆分语句：按分号分割，正确处理多行语句
    # 先去掉单行注释，然后按分号分割
    lines = []
    for line in sql_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        lines.append(stripped)

    # 合并所有内容，然后按分号分割
    full = '\n'.join(lines)
    # 智能分割：不在字符串内部的分号才分割
    statements = []
    current = ''
    in_string = False
    string_char = None
    for char in full:
        if char in ("'", '"') and (not current or current[-1] != '\\'):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
        if char == ';' and not in_string:
            stmt = current.strip()
            if stmt:
                statements.append(stmt)
            current = ''
        else:
            current += char
    stmt = current.strip()
    if stmt:
        statements.append(stmt)

    cursor = conn.cursor()
    success = 0
    errors = []
    for i, stmt in enumerate(statements, 1):
        if not stmt:
            continue
        try:
            cursor.execute(stmt)
            results = cursor.fetchall()
            success += 1
            if results:
                for row in results:
                    vals = [str(v) for v in row[:5]]
                    print(f"   {', '.join(vals)}")
        except Exception as e:
            err_msg = str(e)
            if '1061' in err_msg or 'Duplicate key name' in err_msg:
                print(f"   [SKIP] 索引已存在")
                success += 1
            elif '1060' in err_msg or 'Duplicate column name' in err_msg:
                print(f"   [SKIP] 列已存在")
                success += 1
            elif '1050' in err_msg or 'Table already exists' in err_msg:
                print(f"   [SKIP] 表已存在")
                success += 1
            elif '1064' in err_msg and 'syntax' in err_msg.lower():
                errors.append((i, stmt[:120], err_msg))
                print(f"   [SYNTAX ERROR] 语句{i}: {err_msg[:100]}")
                print(f"      SQL: {stmt[:150]}...")
            else:
                errors.append((i, stmt[:120], err_msg))
                print(f"   [ERROR] 语句{i}: {err_msg[:100]}")

    conn.commit()
    cursor.close()
    print(f"\n 结果: 成功 {success}, 失败 {len(errors)}")
    if errors:
        for i, stmt, err in errors[:5]:
            print(f"   语句{i}: {err[:120]}")
    return len(errors) == 0


def verify(conn):
    print(f"\n{'='*60}")
    print(" >>> 验证数据迁移")
    print(f"{'='*60}")
    cursor = conn.cursor()

    # 1. 子表是否存在
    cursor.execute("""
        SELECT TABLE_NAME FROM information_schema.tables
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ('ai_flight_delay_data', 'ai_baggage_delay_data')
    """, (os.getenv("DB_NAME", "ai"),))
    tables = sorted([r[0] for r in cursor.fetchall()])
    print(f"\n[1] 子表: {tables}")
    assert 'ai_flight_delay_data' in tables, "航班子表不存在!"
    assert 'ai_baggage_delay_data' in tables, "行李子表不存在!"
    print("   OK")

    # 2. 主表新增列
    cursor.execute("""
        SELECT COLUMN_NAME FROM information_schema.columns
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'ai_review_result'
        AND COLUMN_NAME IN ('claim_type','benefit_name','insured_name','final_decision',
                           'manual_status','manual_conclusion','ai_model_version','pipeline_version',
                           'rule_ids_hit','audit_reason_tags','human_override')
        ORDER BY ORDINAL_POSITION
    """, (os.getenv("DB_NAME", "ai"),))
    new_cols = [r[0] for r in cursor.fetchall()]
    print(f"\n[2] 主表新增列 ({len(new_cols)}/11): {new_cols}")

    # 3. 记录数
    cursor.execute("SELECT COUNT(*) FROM ai_review_result")
    main_count = cursor.fetchone()[0]
    print(f"\n[3] 主表总记录数: {main_count}")

    cursor.execute("SELECT COUNT(*) FROM ai_flight_delay_data")
    flight_count = cursor.fetchone()[0]
    print(f"[4] 航班子表记录数: {flight_count}")

    cursor.execute("SELECT COUNT(*) FROM ai_baggage_delay_data")
    baggage_count = cursor.fetchone()[0]
    print(f"[5] 行李子表记录数: {baggage_count}")

    # 4. 孤立记录检查
    cursor.execute("""
        SELECT COUNT(*) FROM ai_flight_delay_data f
        LEFT JOIN ai_review_result r ON f.forceid = r.forceid WHERE r.forceid IS NULL
    """)
    orphaned_f = cursor.fetchone()[0]
    print(f"[6] 航班子表孤立记录: {orphaned_f}")

    cursor.execute("""
        SELECT COUNT(*) FROM ai_baggage_delay_data b
        LEFT JOIN ai_review_result r ON b.forceid = r.forceid WHERE r.forceid IS NULL
    """)
    orphaned_b = cursor.fetchone()[0]
    print(f"[7] 行李子表孤立记录: {orphaned_b}")

    # 5. 航班数据抽样验证（008 后 flight_no 还在主表，可以对比）
    cursor.execute("""
        SELECT r.forceid, r.flight_no, f.flight_no
        FROM ai_review_result r
        JOIN ai_flight_delay_data f ON r.forceid = f.forceid
        WHERE r.flight_no IS NOT NULL AND r.flight_no != ''
        LIMIT 5
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"\n[8] 航班数据抽样验证 ({len(rows)}条):")
        match = True
        for row in rows:
            ok = "OK" if row[1] == row[2] else "MISMATCH"
            print(f"   forceid={row[0]}, 主表={row[1]}, 子表={row[2]} [{ok}]")
            if row[1] != row[2]:
                match = False
        if match:
            print("   OK: 抽样数据一致")
        else:
            print("   WARNING: 有不匹配!")

    # 6. 行李数据抽样验证
    cursor.execute("""
        SELECT r.forceid, r.baggage_receipt_time, b.baggage_receipt_time,
               r.baggage_delay_hours, b.baggage_delay_hours
        FROM ai_review_result r
        JOIN ai_baggage_delay_data b ON r.forceid = b.forceid
        LIMIT 5
    """)
    rows = cursor.fetchall()
    if rows:
        print(f"\n[9] 行李数据抽样验证 ({len(rows)}条):")
        for row in rows:
            rt_ok = "OK" if row[1] == row[2] else "MISMATCH"
            dh_ok = "OK" if row[3] == row[4] else "MISMATCH"
            print(f"   forceid={row[0]}, receipt:主={row[1]},子={row[2]}[{rt_ok}] hours:主={row[3]},子={row[4]}[{dh_ok}]")

    # 7. claim_type 分布
    cursor.execute("SELECT claim_type, COUNT(*) FROM ai_review_result GROUP BY claim_type ORDER BY claim_type")
    rows = cursor.fetchall()
    print(f"\n[10] claim_type 分布:")
    for row in rows:
        label = row[0] if row[0] else '(NULL)'
        print(f"   {label}: {row[1]}")

    cursor.close()
    print(f"\n{'='*60}")
    print(" 验证完成!")
    print(f"{'='*60}")


if __name__ == '__main__':
    print(f"数据库结构改进迁移")
    print(f"目标: {os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}")
    print(f"用户: {os.getenv('DB_USER')}")

    conn = get_conn()
    try:
        # Step 1: 007
        ok7 = run_sql_file(conn, MIGRATIONS['007'], '007 - 新建子表 + 主表新增列')
        if not ok7:
            print("\n!!! 007 有错误，请检查!")

        # Step 2: 008
        ok8 = run_sql_file(conn, MIGRATIONS['008'], '008 - 数据迁移到子表')
        if not ok8:
            print("\n!!! 008 有错误，请检查!")

        # Step 3: 验证
        verify(conn)

        print("\n\n请检查上述验证结果。")
        answer = input("输入 'yes' 继续执行 010(更新视图) 和 009(删除列): ").strip().lower()

        if answer == 'yes':
            # Step 4: 010
            ok10 = run_sql_file(conn, MIGRATIONS['010'], '010 - 更新视图')

            # Step 5: 009
            print("\n即将执行 009 - 删除主表已迁移的列")
            print("删除后主表字段将无法恢复（除非有备份）")
            answer2 = input("输入 'DROP' 确认: ").strip()
            if answer2 == 'DROP':
                ok9 = run_sql_file(conn, MIGRATIONS['009'], '009 - 删除主表已迁移的列')
                if ok9:
                    print("\n=== 全部迁移完成 ===")
            else:
                print("已取消 009 执行")
        else:
            print("已取消后续操作。可以手动继续。")

    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()
        print("\n数据库连接已关闭")
