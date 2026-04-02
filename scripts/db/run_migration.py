#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库迁移执行脚本
用于创建生产化所需的数据库表结构
"""

import os
import sys
import logging
import argparse
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config import config
from app.db.database import get_db_connection, DatabaseError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
LOGGER = logging.getLogger(__name__)


def read_migration_file(migration_path: Path) -> str:
    """读取迁移文件"""
    if not migration_path.exists():
        raise FileNotFoundError(f"迁移文件不存在: {migration_path}")

    with open(migration_path, 'r', encoding='utf-8') as f:
        return f.read()


def split_sql_statements(sql_content: str) -> list:
    """分割SQL语句"""
    # 移除注释和空行
    lines = []
    for line in sql_content.split('\n'):
        line = line.strip()
        if line and not line.startswith('--'):
            lines.append(line)

    # 合并为完整语句
    sql_text = ' '.join(lines)

    # 按分号分割，但排除存储过程中的分号
    statements = []
    current = ''
    in_procedure = False

    for char in sql_text:
        current += char
        if char == ';' and not in_procedure:
            statements.append(current.strip())
            current = ''
        elif 'DELIMITER //' in current:
            # 处理存储过程
            in_procedure = True
        elif 'DELIMITER ;' in current:
            in_procedure = False

    if current.strip():
        statements.append(current.strip())

    return statements


async def execute_migration(migration_path: Path, dry_run: bool = False):
    """执行数据库迁移"""
    LOGGER.info(f"开始执行迁移: {migration_path.name}")

    # 读取迁移文件
    sql_content = read_migration_file(migration_path)
    statements = split_sql_statements(sql_content)

    LOGGER.info(f"找到 {len(statements)} 条SQL语句")

    # 初始化数据库连接
    db = get_db_connection()
    await db.initialize()

    try:
        async with db.get_connection() as conn:
            async with conn.cursor() as cursor:
                for i, statement in enumerate(statements, 1):
                    if dry_run:
                        LOGGER.info(f"[DRY RUN] 语句 {i}: {statement[:100]}...")
                        continue

                    try:
                        LOGGER.info(f"执行语句 {i}/{len(statements)}")
                        await cursor.execute(statement)
                        LOGGER.info(f"✓ 语句 {i} 执行成功")
                    except Exception as e:
                        LOGGER.error(f"✗ 语句 {i} 执行失败: {e}")
                        LOGGER.error(f"失败语句: {statement[:200]}...")
                        raise

        if not dry_run:
            LOGGER.info("✓ 迁移执行完成")
        else:
            LOGGER.info("✓ 迁移预检查完成（DRY RUN）")

    except Exception as e:
        LOGGER.error(f"迁移执行失败: {e}")
        raise
    finally:
        await db.close()


async def check_database_connection():
    """检查数据库连接"""
    LOGGER.info("检查数据库连接...")

    db = get_db_connection()
    try:
        await db.initialize()

        async with db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT 1")
                result = await cursor.fetchone()

                if result and result[0] == 1:
                    LOGGER.info("✓ 数据库连接正常")
                    return True
                else:
                    LOGGER.error("✗ 数据库连接测试失败")
                    return False

    except Exception as e:
        LOGGER.error(f"✗ 数据库连接失败: {e}")
        return False
    finally:
        await db.close()


async def list_existing_tables():
    """列出已存在的表"""
    LOGGER.info("列出数据库中的表...")

    db = get_db_connection()
    try:
        await db.initialize()

        async with db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("""
                    SELECT table_name, table_comment, create_time
                    FROM information_schema.tables
                    WHERE table_schema = %s
                    ORDER BY table_name
                """, (config.DB_NAME,))

                tables = await cursor.fetchall()

                if tables:
                    LOGGER.info(f"数据库 '{config.DB_NAME}' 中的表:")
                    for table in tables:
                        LOGGER.info(f"  - {table[0]} ({table[1]}) - 创建于: {table[2]}")
                else:
                    LOGGER.info("数据库中没有表")

                return tables

    except Exception as e:
        LOGGER.error(f"列出表失败: {e}")
        return []
    finally:
        await db.close()


async def create_database_if_not_exists():
    """如果数据库不存在则创建"""
    LOGGER.info(f"检查数据库 '{config.DB_NAME}' 是否存在...")

    db = get_db_connection()
    try:
        # 先连接到默认数据库
        temp_pool = await aiomysql.create_pool(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            db='mysql',  # 连接到默认数据库
            charset='utf8mb4',
            autocommit=True
        )

        async with temp_pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 检查数据库是否存在
                await cursor.execute("SHOW DATABASES LIKE %s", (config.DB_NAME,))
                exists = await cursor.fetchone()

                if not exists:
                    LOGGER.info(f"数据库 '{config.DB_NAME}' 不存在，正在创建...")
                    await cursor.execute(f"CREATE DATABASE {config.DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                    LOGGER.info(f"✓ 数据库 '{config.DB_NAME}' 创建成功")
                else:
                    LOGGER.info(f"✓ 数据库 '{config.DB_NAME}' 已存在")

        temp_pool.close()
        await temp_pool.wait_closed()

    except Exception as e:
        LOGGER.error(f"创建数据库失败: {e}")
        raise


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='数据库迁移工具')
    parser.add_argument('--migration', type=str, default='001',
                       help='迁移文件编号 (默认: 001)')
    parser.add_argument('--dry-run', action='store_true',
                       help='预运行，不实际执行SQL')
    parser.add_argument('--check-only', action='store_true',
                       help='仅检查数据库连接')
    parser.add_argument('--list-tables', action='store_true',
                       help='列出数据库中的表')
    parser.add_argument('--create-db', action='store_true',
                       help='创建数据库（如果不存在）')

    args = parser.parse_args()

    try:
        # 检查数据库连接
        if not await check_database_connection():
            LOGGER.error("数据库连接失败，请检查配置")
            sys.exit(1)

        # 创建数据库（如果需要）
        if args.create_db:
            await create_database_if_not_exists()

        # 列出表（如果需要）
        if args.list_tables:
            await list_existing_tables()
            return

        # 仅检查连接（如果需要）
        if args.check_only:
            LOGGER.info("数据库连接检查完成")
            return

        # 执行迁移
        migration_dir = project_root / 'scripts' / 'db' / 'migrations'
        migration_file = migration_dir / f'{args.migration}_create_production_tables.sql'

        if not migration_file.exists():
            # 尝试其他命名格式
            migration_file = migration_dir / f'{args.migration}.sql'
            if not migration_file.exists():
                LOGGER.error(f"迁移文件不存在: {migration_file}")
                sys.exit(1)

        await execute_migration(migration_file, args.dry_run)

    except DatabaseError as e:
        LOGGER.error(f"数据库错误: {e}")
        sys.exit(1)
    except FileNotFoundError as e:
        LOGGER.error(f"文件错误: {e}")
        sys.exit(1)
    except Exception as e:
        LOGGER.error(f"未知错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())