#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填历史数据：将当前 ai_review_result 的 AI 审核结果补入 ai_review_history"""
import os
import sys
import json
import pymysql
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


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


def backfill():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM ai_review_result
                WHERE audit_result IS NOT NULL AND audit_result != ''
                ORDER BY created_at
            """)
            rows = cur.fetchall()
        print(f"待回填 AI 审核记录: {len(rows)} 条")

        # 已存在于历史表的 forceid
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT forceid FROM ai_review_history WHERE review_type='ai'")
            existing = {r['forceid'] for r in cur.fetchall()}
        print(f"历史表中已有 AI 记录: {len(existing)} 条")

        new_count = 0
        for row in rows:
            fid = row['forceid']
            if fid in existing:
                continue

            fields = {
                'forceid': fid,
                'claim_id': row.get('claim_id'),
                'benefit_name': row.get('benefit_name'),
                'review_type': 'ai',
                'audit_result': row.get('audit_result'),
                'audit_status': row.get('audit_status'),
                'confidence_score': row.get('confidence_score'),
                'payout_amount': row.get('payout_amount'),
                'identity_match': row.get('identity_match'),
                'threshold_met': row.get('threshold_met'),
                'exclusion_triggered': row.get('exclusion_triggered'),
                'manual_status': row.get('manual_status'),
                'manual_conclusion': row.get('manual_conclusion'),
                'ai_model_version': row.get('ai_model_version'),
                'pipeline_version': row.get('pipeline_version'),
                'rule_ids_hit': row.get('rule_ids_hit'),
                'audit_time': row.get('audit_time'),
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
            }

            snapshot = {k: v for k, v in row.items() if v is not None}
            fields['snapshot_json'] = json.dumps(snapshot, ensure_ascii=False, default=str)

            keys = list(fields.keys())
            placeholders = ', '.join(['%s'] * len(keys))
            values = []
            for k, v in fields.items():
                if v is None:
                    values.append(None)
                elif isinstance(v, (int, float)):
                    values.append(v)
                else:
                    values.append(str(v))

            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO ai_review_history ({', '.join(keys)}) VALUES ({placeholders})",
                    values,
                )
            new_count += 1

        conn.commit()
        print(f"回填完成: 新增 {new_count} 条 AI 历史记录")
        return new_count

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    backfill()
