---
title: 航班延误赔付规则
created: 2026-05-05
updated: 2026-05-05
type: concept
tags: [flight-delay, compensation, tier-lookup, exclusion]
sources: [app/rules/claim_types/flight_delay.py, app/modules/flight_delay/stages/payout.py, app/modules/flight_delay/stages/delay_calc.py]
confidence: high
---

# 航班延误赔付规则

## 赔付档位 (Tier Lookup)

起赔门槛：**延误满 5 小时**。未达 5 小时 → 拒赔。

| 档位 | 延误时长 | 赔付金额 |
|------|----------|----------|
| 第一档 | 5h ≤ T < 10h | 300 元 |
| 第二档 | 10h ≤ T < 15h | 600 元 |
| 第三档 | 15h ≤ T < 20h | 900 元 |
| 第四档 | T ≥ 20h | 1200 元（最高保额） |

档位配置定义在 `app/rules/claim_types/flight_delay.py` 的 `FLIGHT_DELAY_TIERS`，由 `app/skills/compensation.py` 的 `tier_lookup()` 统一查表。

## 延误时长计算 — 取长原则（核心）

取以下两者的**较长者**作为赔付时长：

- **a)** 自原订开出时间起算，至实际（或改签后）开出时间
- **b)** 自原订到达时间起算，至实际（或改签后）抵达原计划目的地的到达时间

实现位于 `app/modules/flight_delay/stages/delay_calc.py` 的 `_compute_delay_minutes()`。

## 三种计算口径

### 口径1：旅客首版计划 → 飞常准实际
- 条件：存在 `schedule_revision_chain` 且非联程场景
- 取 chain[0] 的计划时间 vs actual_local 的实际时间
- 联程/中转场景下跳过此口径（chain[0] 为首段，actual_local 为理赔焦点航班，描述不同航班）

### 口径2：计划 → 替代航班 alt（改签场景）
- 基准时间：以原始被取消航班的计划起飞/到达时间为基准
- 结束时间：以改签后实际乘坐航班的实际起飞/到达时间
- **机场匹配**：只计算原航班与替代航班在同一机场（出发/到达）对应的延误分量
- 联程场景：用末段机场（last_seg_dep/arr_iata）做匹配

### 口径3：计划 → 飞常准实际（兜底）
- 直接比较 schedule_local 的计划时间 vs actual_local 的实际时间

### 最终取值
- 联程改签：取 max(口径2 的 c, d)
- 口径2 和口径3 都有值：取 max(口径2, 口径3)
- 仅口径2 有值：取口径2
- 仅口径3 有值：取口径3
- 都没有：文本提取兜底（从案件描述中提取延误分钟数）

## 改签场景特殊规则

- **严禁**将改签航班自身的运营延误视为索赔延误时长
- 联程改签（原航班取消后改签为联程航班）：延误 = max(联程首段实际起飞 - 原航班计划起飞, 联程末段实际到达 - 原航班计划到达)
- 单段直飞不标记为联程改签（校验 `itinerary_segments` 数量）

## 赔付金额计算

`app/modules/flight_delay/stages/payout.py` 的 `_run_payout_calc()`：
1. 从 `computed_delay.final_minutes` 获取延误分钟数
2. 调用 `calculate_payout()` 查 tier → calculated_amount
3. 索赔金额 > 计算金额 → 按计算金额；索赔金额 < 计算金额 → 按索赔金额
4. 不超过 insured_amount（保单限额）

## 硬校验规则

`app/modules/flight_delay/stages/hardcheck.py` 的 `_run_hardcheck()` 包含以下硬校验：

| 校验项 | 触发条件 | 结果 |
|--------|---------|------|
| 纯国内航班 | dep_cc=CN 且 arr_cc=CN | 拒赔 |
| 战争风险 | 出发/到达/中转国家命中战争维护表 | 拒赔 |
| 保单有效期 | 所有时间点均不在有效期内 | 拒赔 |
| 承保区域 | 航班不在保险计划承保区域内 | 拒赔 |
| 境内中转 | 中转地在境内 | 拒赔 |
| 中转接驳免责 | 前序航班延误导致误机 | 拒赔 |
| 非客运航班 | 非民航客运班机 | 拒赔 |
| 同天投保 | 出境当天投保且投保时刻 ≥ 计划起飞 | 拒赔 |
| 姓名不符 | 登机牌/延误证明乘客姓名 ≠ 保单被保险人 | 拒赔 |
| 可预见因素欺诈 | 投保时延误因素已可预见（台风/罢工等） | 拒赔/人工复核 |

## 相关页面
- [[flight-delay-module]] — 航班延误模块
- [[rule-system]] — 规则系统
- [[pipeline-architecture]] — Pipeline 架构
- [[shared-rules-usage]] — 共享规则使用情况
- [[flight-lookup-skill]] — 飞常准航班查询技能
