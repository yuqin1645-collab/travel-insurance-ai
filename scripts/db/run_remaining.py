#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行剩余的 010 和 009 迁移"""
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()

def get_conn():
    return pymysql.connect(
        host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME", "ai"), charset="utf8mb4",
    )

def run_sql_file(conn, path, name):
    print(f"\n{'='*60}")
    print(f" >>> 执行: {name}")
    print(f"{'='*60}")
    with open(path, 'r', encoding='utf-8') as f:
        sql_text = f.read()

    lines = []
    for line in sql_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        lines.append(stripped)
    full = '\n'.join(lines)

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
    if current.strip():
        statements.append(current.strip())

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
            if any(code in err_msg for code in ['1060', '1061', '1050', 'Duplicate']):
                print(f"   [SKIP] 已存在: {err_msg[:80]}")
                success += 1
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

def final_verify(conn):
    print(f"\n{'='*60}")
    print(" >>> 最终验证")
    print(f"{'='*60}")
    cursor = conn.cursor()

    # 主表现在的列
    cursor.execute("""
        SELECT COLUMN_NAME FROM information_schema.columns
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'ai_review_result'
        ORDER BY ORDINAL_POSITION
    """, (os.getenv("DB_NAME", "ai"),))
    cols = [r[0] for r in cursor.fetchall()]
    print(f"\n主表现有 {len(cols)} 列:")
    for c in cols:
        print(f"  {c}")

    # 子表列数
    cursor.execute("""
        SELECT TABLE_NAME, COUNT(*) FROM information_schema.columns
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ('ai_flight_delay_data', 'ai_baggage_delay_data')
        GROUP BY TABLE_NAME
    """, (os.getenv("DB_NAME", "ai"),))
    for r in cursor.fetchall():
        print(f"\n{r[0]}: {r[1]} 列")

    # 记录数
    cursor.execute("SELECT COUNT(*) FROM ai_review_result")
    print(f"\n主表记录: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM ai_flight_delay_data")
    print(f"航班子表: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM ai_baggage_delay_data")
    print(f"行李子表: {cursor.fetchone()[0]}")

    # claim_type
    cursor.execute("SELECT claim_type, COUNT(*) FROM ai_review_result GROUP BY claim_type")
    print("\nclaim_type 分布:")
    for r in cursor.fetchall():
        print(f"  {r[0]}: {r[1]}")

    cursor.close()
    print(f"\n{'='*60}")

if __name__ == '__main__':
    conn = get_conn()
    try:
        # 010 更新视图
        ok10 = run_sql_file(conn, 'scripts/db/migrations/010_update_views.sql', '010 - 更新视图')

        # 009 删除列（用户已确认执行全部迁移）
        print("\n[!] 执行 009 - 删除主表已迁移的列")
        ok9 = run_sql_file(conn, 'scripts/db/migrations/009_drop_migrated_columns.sql', '009 - 删除主表已迁移的列')
        if ok9:
            print("\n=== 全部迁移完成！===")
            final_verify(conn)
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()
        print("\n数据库连接已关闭")
