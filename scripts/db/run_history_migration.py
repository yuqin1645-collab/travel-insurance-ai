#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行审核历史迁移: 011 + 012
"""
import os
import sys
import pymysql
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS = [
    'scripts/db/migrations/011_add_review_history.sql',
    'scripts/db/migrations/012_add_history_views.sql',
]


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

    lines = []
    for line in sql_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('--'):
            continue
        lines.append(stripped)

    full = '\n'.join(lines)
    statements = [s.strip() for s in full.split(';') if s.strip()]

    success = 0
    skipped = 0
    for stmt in statements:
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
            # 检查是否返回了状态消息（SELECT ... AS status）
            result = cur.fetchall()
            if result:
                for row in result:
                    print(f"  {row}")
            success += 1
        except Exception as e:
            err_str = str(e)
            if 'Duplicate column' in err_str or '1060' in err_str:
                skipped += 1
                print(f"  [跳过] 列已存在")
            elif 'Duplicate key name' in err_str or '1061' in err_str:
                skipped += 1
                print(f"  [跳过] 索引已存在")
            elif 'Table already exists' in err_str or '1050' in err_str:
                skipped += 1
                print(f"  [跳过] 表已存在")
            else:
                print(f"  [ERROR] {e}")
                conn.rollback()
                return False, 0, 0

    conn.commit()
    return True, success, skipped


def main():
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(ROOT)

    conn = get_conn()
    print(f"已连接数据库: {os.getenv('DB_HOST')}/{os.getenv('DB_NAME', 'ai')}")

    total_success = 0
    total_skipped = 0

    for mig_path in MIGRATIONS:
        full_path = os.path.join(ROOT, mig_path)
        name = os.path.basename(mig_path)

        ok, success, skipped = run_sql_file(conn, full_path, name)
        if not ok:
            print(f"\n迁移失败: {name}")
            conn.close()
            sys.exit(1)
        total_success += success
        total_skipped += skipped

    conn.close()
    print(f"\n{'='*60}")
    print(f" 迁移完成: 成功 {total_success} 条语句, 跳过 {total_skipped} 条 (已存在)")
    print(f"{'='*60}")

    # 验证
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'ai_review_history'")
        if cur.fetchone():
            print("  [OK] ai_review_history 表已创建")
        else:
            print("  [FAIL] ai_review_history 表未找到")

        cur.execute("SHOW COLUMNS FROM ai_review_result LIKE 'first_ai_audit_time'")
        if cur.fetchone():
            print("  [OK] first_ai_audit_time 列已添加")
        else:
            print("  [FAIL] first_ai_audit_time 列未找到")

        cur.execute("SHOW TABLES LIKE 'v_ai_vs_manual_comparison'")
        if cur.fetchone():
            print("  [OK] v_ai_vs_manual_comparison 视图已创建")
        else:
            print("  [WARN] v_ai_vs_manual_comparison 视图未找到 (可忽略)")

    conn.close()


if __name__ == "__main__":
    main()
