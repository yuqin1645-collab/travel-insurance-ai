# Wiki Log

> Chronological record of all wiki actions. Append-only.
> Format: `## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete
> When this file exceeds 500 entries, rotate: rename to log-YYYY.md, start fresh.

## [2026-05-05] create | Wiki initialized
- Domain: 旅行保险AI自动审核系统 (travel-insurance-ai)
- Structure created with SCHEMA.md, index.md, log.md
- Source repo: yuqin1645-collab/travel-insurance-ai

## [2026-05-05] ingest | CLAUDE.md
- Source: raw/articles/claude-md.md
- Created: concepts/pipeline-architecture.md, concepts/rule-system.md, concepts/prompt-system.md
- Created: entities/ruleresult.md, entities/ops-scripts.md
- Updated: index.md (5 pages total)

## [2026-05-05] ingest | 行李延误规则 + 行李延误模块
- Source: raw/articles/baggage-delay-rules.md, raw/articles/baggage-delay-pipeline.md
- Created: concepts/baggage-delay-compensation.md, entities/baggage-delay-module.md
- Updated: index.md (7 pages total)

## [2026-05-05] ingest | 航班延误规则 + 共享组件 + 行李损坏 + 飞常准 + 差异追踪
- Sources: app/rules/claim_types/flight_delay.py, app/modules/flight_delay/pipeline.py, app/modules/flight_delay/stages/*, app/skills/flight_lookup.py, app/modules/baggage_damage/*, app/rules/common/*, app/rules/flight/exclusions.py, docs/issue_cluster_tracker.md
- Created: concepts/flight-delay-compensation.md — 航班延误赔付规则（四档阶梯、取长原则、三种口径、硬校验）
- Created: entities/flight-delay-module.md — 航班延误模块（12阶段流程、目录结构）
- Created: entities/baggage-damage-module.md — 行李损坏/随身财产模块（AuditPipeline、11文件、4阶段）
- Created: concepts/shared-rules-usage.md — 共享规则使用情况（四大规则、使用矩阵、提示词块）
- Created: entities/flight-lookup-skill.md — 飞常准航班查询技能（MCP API、缓存、时区）
- Created: concepts/ai-vs-human-diff-tracking.md — AI vs 人工差异追踪（P0清零、P1/P2分析）
- Updated: index.md (13 pages total)
