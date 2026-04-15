# MySQL 数据库字段总览

## 数据库表结构概览

该系统共有 **5 个主要表**和 **2 个视图**：

### 主表
1. **ai_claim_status** - 案件状态管理表
2. **ai_review_result** - AI审核结果主表（核心）
3. **ai_supplementary_records** - 补件记录表
4. **ai_scheduler_logs** - 定时任务日志表
5. **ai_status_history** - 状态变更历史表

### 视图
1. **v_claim_audit_summary** - 案件审核汇总视图
2. **v_audit_statistics** - 审核统计视图

---

## 详细字段列表

### 1. ai_claim_status（案件状态管理表）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键ID |
| claim_id | VARCHAR(64) | | 上游案件ID |
| forceid | VARCHAR(64) | | 案件唯一ID |
| claim_type | VARCHAR(32) | 'flight_delay' | 案件类型 |
| current_status | VARCHAR(32) | 'download_pending' | 当前状态 |
| previous_status | VARCHAR(32) | NULL | 上一状态 |
| status_changed_at | DATETIME | | 状态变更时间 |
| download_status | VARCHAR(32) | 'pending' | 下载状态 |
| download_attempts | INT | 0 | 下载尝试次数 |
| last_download_time | DATETIME | NULL | 最后下载时间 |
| review_status | VARCHAR(32) | 'pending' | 审核状态 |
| review_attempts | INT | 0 | 审核尝试次数 |
| last_review_time | DATETIME | NULL | 最后审核时间 |
| supplementary_count | INT | 0 | 补件次数 |
| max_supplementary | INT | 3 | 最大补件次数 |
| next_check_time | DATETIME | NULL | 下次检查时间 |
| error_message | TEXT | NULL | 错误信息 |
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**：
- UNIQUE KEY uk_forceid (forceid)
- UNIQUE KEY uk_claim_id (claim_id)
- KEY idx_current_status (current_status)
- KEY idx_download_status (download_status)
- KEY idx_review_status (review_status)
- KEY idx_next_check_time (next_check_time)
- KEY idx_claim_type (claim_type)

---

### 2. ai_review_result（AI审核结果主表）

#### 基础信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键ID |
| forceid | VARCHAR(64) | | 案件唯一ID |
| claim_id | VARCHAR(64) | NULL | 上游案件ID |

#### 被保险人信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| passenger_name | VARCHAR(128) | NULL | 被保险人姓名 |
| passenger_id_type | VARCHAR(32) | NULL | 证件类型 |
| passenger_id_number | VARCHAR(64) | NULL | 证件号码 |

#### 保单信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| policy_no | VARCHAR(64) | NULL | 保单号 |
| insurer | VARCHAR(128) | NULL | 保险公司 |
| policy_effective_date | DATE | NULL | 保单生效日期 |
| policy_expiry_date | DATE | NULL | 保单截止日期 |

#### 航班信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| flight_no | VARCHAR(32) | NULL | 航班号 |
| operating_carrier | VARCHAR(128) | NULL | 承运人 |
| dep_iata | VARCHAR(8) | NULL | 出发地IATA |
| arr_iata | VARCHAR(8) | NULL | 目的地IATA |
| dep_city | VARCHAR(64) | NULL | 出发城市 |
| arr_city | VARCHAR(64) | NULL | 目的城市 |
| dep_country | VARCHAR(32) | NULL | 出发国家 |
| arr_country | VARCHAR(32) | NULL | 目的国家 |

#### 航班时间
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| planned_dep_time | DATETIME | NULL | 计划起飞时间 |
| actual_dep_time | DATETIME | NULL | 实际起飞时间 |
| planned_arr_time | DATETIME | NULL | 计划到达时间 |
| actual_arr_time | DATETIME | NULL | 实际到达时间 |
| alt_dep_time | DATETIME | NULL | 替代航班起飞时间 |
| alt_arr_time | DATETIME | NULL | 替代航班到达时间 |

#### 延误计算
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| delay_duration_minutes | INT | NULL | 延误时长(分钟) |
| delay_reason | VARCHAR(128) | NULL | 延误原因 |
| delay_type | VARCHAR(32) | NULL | 延误类型 |

#### 审核结果
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| audit_result | VARCHAR(32) | NULL | 审核结果: 通过/拒绝/需补件 |
| audit_status | VARCHAR(32) | 'pending' | 审核状态 |
| confidence_score | DECIMAL(5,2) | NULL | 置信度(%) |
| audit_time | DATETIME | NULL | 审核时间 |
| auditor | VARCHAR(64) | 'AI系统' | 审核员 |

#### 赔付信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| payout_amount | DECIMAL(10,2) | NULL | 赔付金额 |
| payout_currency | VARCHAR(8) | 'CNY' | 赔付币种 |
| payout_basis | VARCHAR(256) | NULL | 赔付依据 |
| insured_amount | DECIMAL(10,2) | NULL | 保额 |
| remaining_coverage | DECIMAL(10,2) | NULL | 剩余保额 |

#### 补件信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| is_additional | CHAR(1) | 'N' | 是否需要补件: Y/N |
| supplementary_count | INT | 0 | 补件次数 |
| supplementary_reason | TEXT | NULL | 补件原因 |
| supplementary_deadline | DATETIME | NULL | 补件截止时间 |

#### 审核结论
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| remark | VARCHAR(2000) | NULL | 审核备注 |
| key_conclusions | LONGTEXT | NULL | 各核对点结论(JSON) |
| decision_reason | TEXT | NULL | 核赔意见 |

#### 逻辑校验
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| identity_match | CHAR(1) | NULL | 身份是否匹配 |
| threshold_met | CHAR(1) | NULL | 是否达到赔付门槛 |
| exclusion_triggered | CHAR(1) | NULL | 是否有免责情形 |
| exclusion_reason | VARCHAR(256) | NULL | 免责原因 |

#### 前端推送状态
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| forwarded_to_frontend | BOOLEAN | FALSE | 是否已推送到前端 |
| forwarded_at | DATETIME | NULL | 推送时间 |
| frontend_response | TEXT | NULL | 前端响应 |

#### 原始数据
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| raw_result | LONGTEXT | NULL | 完整原始JSON |

#### 元数据
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| metadata | JSON | NULL | 扩展元数据 |

#### 时间戳
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**：
- UNIQUE KEY uk_forceid (forceid)
- KEY idx_claim_id (claim_id)
- KEY idx_audit_result (audit_result)
- KEY idx_audit_status (audit_status)
- KEY idx_is_additional (is_additional)
- KEY idx_passenger_name (passenger_name)
- KEY idx_flight_no (flight_no)
- KEY idx_policy_no (policy_no)
- KEY idx_insurer (insurer)
- KEY idx_delay_duration (delay_duration_minutes)
- KEY idx_payout_amount (payout_amount)
- KEY idx_audit_time (audit_time)
- KEY idx_forwarded_to_frontend (forwarded_to_frontend)
- KEY idx_created_at (created_at)

---

### 3. ai_supplementary_records（补件记录表）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键ID |
| claim_id | VARCHAR(64) | | 案件ID |
| forceid | VARCHAR(64) | | 案件唯一ID |
| supplementary_number | INT | | 第几次补件 |
| requested_at | DATETIME | | 补件请求时间 |
| requested_reason | TEXT | | 补件原因 |
| required_materials | JSON | | 所需材料列表 |
| deadline | DATETIME | | 补件截止时间 |
| completed_at | DATETIME | NULL | 补件完成时间 |
| completed_materials | JSON | NULL | 已补材料列表 |
| status | VARCHAR(32) | 'pending' | 状态 |
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | DATETIME | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**：
- KEY idx_claim_id (claim_id)
- KEY idx_forceid (forceid)
- KEY idx_status (status)
- KEY idx_deadline (deadline)

---

### 4. ai_scheduler_logs（定时任务日志表）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键ID |
| task_type | VARCHAR(32) | | 任务类型 |
| start_time | DATETIME | | 开始时间 |
| end_time | DATETIME | NULL | 结束时间 |
| status | VARCHAR(32) | | 状态 |
| processed_count | INT | 0 | 处理数量 |
| success_count | INT | 0 | 成功数量 |
| failed_count | INT | 0 | 失败数量 |
| error_message | TEXT | NULL | 错误信息 |
| duration_seconds | INT | NULL | 耗时(秒) |
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |

**索引**：
- KEY idx_task_type (task_type)
- KEY idx_start_time (start_time)
- KEY idx_status (status)

---

### 5. ai_status_history（状态变更历史表）

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键ID |
| claim_id | VARCHAR(64) | | 案件ID |
| forceid | VARCHAR(64) | | 案件唯一ID |
| from_status | VARCHAR(32) | NULL | 原状态 |
| to_status | VARCHAR(32) | | 新状态 |
| changed_by | VARCHAR(64) | 'system' | 变更者 |
| change_reason | TEXT | NULL | 变更原因 |
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |

**索引**：
- KEY idx_claim_id (claim_id)
- KEY idx_forceid (forceid)
- KEY idx_created_at (created_at)

---

### 6. v_claim_audit_summary（案件审核汇总视图）

这个视图包含以下字段：
- 案件ID、案件编号
- 被保险人、证件号
- 航班号、出发/目的城市
- 计划/实际起飞时间
- 延误(分钟)、延误原因
- 保险公司、保单号
- 审核结果、赔付金额、币种
- 需补件、补件原因
- 审核时间、审核员
- 流程状态、下载状态、审核状态
- 创建时间

### 7. v_audit_statistics（审核统计视图）

这个视图包含以下统计字段：
- 日期
- 总审核数
- 通过数
- 拒绝数
- 需补件数
- 平均延误(分钟)
- 总赔付金额

---

## 字段统计

### 按表统计字段数量
- ai_claim_status: **18个字段**
- ai_review_result: **45个字段**（核心表）
- ai_supplementary_records: **13个字段**
- ai_scheduler_logs: **10个字段**
- ai_status_history: **8个字段**

### 按类别统计
1. **基础标识字段**: 5个（id, forceid, claim_id等）
2. **被保险人信息**: 3个
3. **保单信息**: 4个
4. **航班信息**: 8个
5. **时间信息**: 8个
6. **审核结果**: 5个
7. **赔付信息**: 5个
8. **补件信息**: 4个
9. **审核结论**: 3个
10. **逻辑校验**: 4个
11. **推送状态**: 3个
12. **原始数据**: 2个
13. **元数据**: 1个

### 数据类型分布
- VARCHAR: 27个（字符串类型）
- DATETIME: 18个（日期时间）
- INT: 12个（整数）
- TEXT/LONGTEXT: 5个（长文本）
- DECIMAL: 5个（小数）
- BOOLEAN: 1个（布尔值）
- JSON: 2个（JSON类型）
- DATE: 2个（日期）

---
*更新时间: 2026-04-02*