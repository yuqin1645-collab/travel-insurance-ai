---
title: 运维脚本体系
created: 2026-05-05
updated: 2026-05-05
type: entity
tags: [review, push, sync, download, report, operations]
sources: [raw/articles/claude-md.md]
confidence: high
---

# 运维脚本体系

## 统一入口脚本（推荐）

| 脚本 | 用途 | 典型用法 |
|------|------|---------|
| `scripts/review.py` | 统一审核入口（批量/重跑/统计） | `--forceid xxx` 重跑指定案件 |
| `scripts/push.py` | 统一推送入口（前端+数据库） | `--sync-db` 同步数据库 |
| `scripts/report.py` | 统一报表入口 | `--type compare` AI vs 人工对比 |
| `scripts/query.py` | 统一查询入口 | `forceid xxx` 查案件 |
| `scripts/data.py` | 统一数据管理入口 | `download` 全量下载 |

## 重要规则
- **重跑案件必须使用 `review.py --forceid`**，禁止单独用 `push.py` 做数据库同步
- 日常增量同步用 `sync_claims_from_api.py --no-delete`

## 相关页面
- [[pipeline-architecture]] — Pipeline 架构
- [[rule-system]] — 规则系统
