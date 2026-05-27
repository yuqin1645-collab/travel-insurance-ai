#!/usr/bin/env python3
"""
清理 ai_review_history 表中的重复记录
保留每个 forceid 的最新记录
"""

import pymysql
import os
from dotenv import load_dotenv

# 加载配置
load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'ai'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)

def analyze_duplicates():
    """分析重复记录情况"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 统计每个 forceid 的记录数
            cur.execute("""
                SELECT forceid, COUNT(*) as cnt
                FROM ai_review_history
                GROUP BY forceid
                HAVING cnt > 1
                ORDER BY cnt DESC
            """)
            duplicates = cur.fetchall()
            
            print(f"\n{'='*60}")
            print(f"重复记录分析")
            print(f"{'='*60}")
            print(f"有重复记录的案件数: {len(duplicates)}")
            
            # 统计总记录数
            cur.execute("SELECT COUNT(*) as total FROM ai_review_history")
            total = cur.fetchone()['total']
            print(f"历史记录总数: {total}")
            
            # 统计唯一 forceid 数
            cur.execute("SELECT COUNT(DISTINCT forceid) as unique_count FROM ai_review_history")
            unique = cur.fetchone()['unique_count']
            print(f"唯一案件数: {unique}")
            
            # 计算可删除的重复数
            total_duplicates = sum(row['cnt'] - 1 for row in duplicates)
            print(f"可删除的重复记录数: {total_duplicates}")
            
            print(f"\n重复记录最多的案件 TOP 10:")
            for row in duplicates[:10]:
                print(f"  {row['forceid']}: {row['cnt']} 次")
            
            return duplicates
    finally:
        conn.close()

def clean_duplicates(dry_run=True):
    """清理重复记录，保留每个 forceid 的最新记录"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 找出每个 forceid 的非最新记录 ID
            cur.execute("""
                SELECT h1.id
                FROM ai_review_history h1
                INNER JOIN (
                    SELECT forceid, MAX(id) as max_id
                    FROM ai_review_history
                    GROUP BY forceid
                ) h2 ON h1.forceid = h2.forceid AND h1.id < h2.max_id
            """)
            duplicates_to_delete = cur.fetchall()
            
            if dry_run:
                print(f"\n{'='*60}")
                print(f"清理预览 (dry_run=True)")
                print(f"{'='*60}")
                print(f"将删除 {len(duplicates_to_delete)} 条重复记录")
                print(f"\n将被删除的记录 ID (前20条):")
                for row in duplicates_to_delete[:20]:
                    print(f"  ID: {row['id']}")
                if len(duplicates_to_delete) > 20:
                    print(f"  ... 还有 {len(duplicates_to_delete) - 20} 条")
            else:
                # 执行删除
                ids_to_delete = [row['id'] for row in duplicates_to_delete]
                if ids_to_delete:
                    # 分批删除，避免 SQL 语句过长
                    batch_size = 500
                    for i in range(0, len(ids_to_delete), batch_size):
                        batch = ids_to_delete[i:i+batch_size]
                        placeholders = ','.join(['%s'] * len(batch))
                        cur.execute(f"DELETE FROM ai_review_history WHERE id IN ({placeholders})", batch)
                    conn.commit()
                    print(f"\n已删除 {len(ids_to_delete)} 条重复记录")
                
                # 验证结果
                cur.execute("SELECT COUNT(*) as total FROM ai_review_history")
                remaining = cur.fetchone()['total']
                print(f"剩余记录数: {remaining}")
                
    finally:
        conn.close()

if __name__ == "__main__":
    print("分析重复记录...")
    analyze_duplicates()
    
    print("\n执行清理预览...")
    clean_duplicates(dry_run=True)
    
    # 取消下面的注释以执行实际清理
    print("\n执行实际清理...")
    clean_duplicates(dry_run=False)
