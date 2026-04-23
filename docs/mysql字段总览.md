# MySQL 数据库字段总览

## 数据库表结构概览

该系统共有 **5 个主要表**和 **2 个视图**：

### 主表
1. **ai_claim_status** - 案件状态管理表
2. **ai_review_result** - AI审核结果主表（核心）
3. **ai_review_segments** - 联程航段子表（一对多，forceid 关联）
4. **ai_supplementary_records** - 补件记录表
5. **ai_scheduler_logs** - 定时任务日志表
6. **ai_status_history** - 状态变更历史表
7. **ai_claim_info_raw** - 案件原始下载信息存档表（数据追溯备份）

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

> 当前共 **87 个字段**，航班延误和行李延误共用此表，通过 `benefit_name` 区分险种。

#### 基础信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键ID |
| forceid | VARCHAR(64) | | 案件唯一ID |
| claim_id | VARCHAR(64) | NULL | 上游案件ID |
| benefit_name | VARCHAR(64) | NULL | 险种名称（如"航班延误"、"行李延误"） |

#### 被保险人信息
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| passenger_name | VARCHAR(128) | NULL | 被保险人姓名（来自材料/审核结果） |
| insured_name | VARCHAR(128) | NULL | 被保险人姓名（来自 claim_info） |
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
| flight_no | VARCHAR(32) | NULL | 原航班号 |
| operating_carrier | VARCHAR(128) | NULL | 承运人 |
| dep_iata | VARCHAR(8) | NULL | 原航班出发IATA |
| arr_iata | VARCHAR(8) | NULL | 原航班到达IATA |
| dep_city | VARCHAR(64) | NULL | 出发城市 |
| arr_city | VARCHAR(64) | NULL | 目的城市 |
| dep_country | VARCHAR(32) | NULL | 出发国家 |
| arr_country | VARCHAR(32) | NULL | 目的国家 |

#### 原航班时间（来自 schedule_local / actual_local）
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| planned_dep_time | DATETIME | NULL | 原航班首次购票计划起飞（schedule_local，延误计算基准） |
| planned_arr_time | DATETIME | NULL | 原航班首次购票计划到达 |
| actual_dep_time | DATETIME | NULL | 原航班实际起飞（飞常准优先） |
| actual_arr_time | DATETIME | NULL | 原航班实际到达（飞常准优先；行李延误险亦用此字段存首次乘坐航班实际到达时间） |

#### 实际乘坐航班（改签/替代，来自 alternate_local）
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| alt_dep_time | DATETIME | NULL | 被保险人最终乘坐航班实际起飞 |
| alt_arr_time | DATETIME | NULL | 被保险人最终乘坐航班实际到达 |
| alt_flight_no | VARCHAR(32) | NULL | 被保险人实际乘坐的改签航班号 |
| alt_dep_iata | VARCHAR(8) | NULL | 实际乘坐航班出发IATA |
| alt_arr_iata | VARCHAR(8) | NULL | 实际乘坐航班到达IATA |

#### 行李延误专属字段
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| baggage_receipt_time | DATETIME | NULL | 行李签收时间（延误终止点） |
| baggage_delay_hours | DECIMAL(5,1) | NULL | 行李延误小时数 |
| has_baggage_delay_proof | CHAR(1) | NULL | 是否有行李延误证明（Y/N） |
| has_baggage_receipt_proof | CHAR(1) | NULL | 是否有签收时间证明（Y/N） |
| has_baggage_tag_proof | CHAR(1) | NULL | 是否有行李牌（Y/N） |
| pir_no | VARCHAR(64) | NULL | PIR 不正常行李报告编号/来源描述 |

#### 航班场景
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| flight_scenario | VARCHAR(32) | NULL | 航班场景：direct/connecting/rebooking/multi_rebooking/cancelled_nofly |
| rebooking_count | TINYINT | 0 | 改签次数（0=无改签） |

#### 联程信息（汇总标量，详情见 ai_review_segments 子表）
> `flight_no` / `dep_iata` / `arr_iata` 始终记录**触发延误的那段**；行程首尾用 `origin_iata` / `destination_iata` 表示。

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| is_connecting | TINYINT(1) | NULL | 是否联程（1=联程，0=直飞，NULL=未判断） |
| total_segments | TINYINT | NULL | 联程总段数（直飞=1，两段联程=2） |
| origin_iata | VARCHAR(8) | NULL | 全程出发机场IATA（联程首段起飞地） |
| destination_iata | VARCHAR(8) | NULL | 全程目的地IATA（联程末段落地） |
| missed_connection | TINYINT(1) | NULL | 是否联程接驳失误（前段延误导致错过后段，1=是） |

#### 飞常准原航班独立字段
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| avi_status | VARCHAR(32) | NULL | 飞常准原航班状态（正常/延误/取消） |
| avi_planned_dep | DATETIME | NULL | 飞常准：原航班计划起飞 |
| avi_planned_arr | DATETIME | NULL | 飞常准：原航班计划到达 |
| avi_actual_dep | DATETIME | NULL | 飞常准：原航班实际起飞 |
| avi_actual_arr | DATETIME | NULL | 飞常准：原航班实际到达 |

#### 飞常准替代航班独立字段
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| avi_alt_flight_no | VARCHAR(32) | NULL | 飞常准查到的替代航班号 |
| avi_alt_planned_dep | DATETIME | NULL | 飞常准：替代航班计划起飞 |
| avi_alt_actual_dep | DATETIME | NULL | 飞常准：替代航班实际起飞 |
| avi_alt_actual_arr | DATETIME | NULL | 飞常准：替代航班实际到达 |

#### 延误计算
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| delay_duration_minutes | INT | NULL | 延误时长（分钟）；行李延误险由 baggage_delay_hours×60 换算写入 |
| delay_reason | VARCHAR(128) | NULL | 延误原因 |
| delay_type | VARCHAR(32) | NULL | 延误类型 |
| delay_calc_from | VARCHAR(64) | NULL | 延误起算时间点来源字段名（如 avi_planned_dep） |
| delay_calc_to | VARCHAR(64) | NULL | 延误终止时间点来源字段名（如 alt_arr_time） |

#### 审核结果
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| audit_result | VARCHAR(32) | NULL | AI 审核结果（通过/拒绝/需补件） |
| audit_status | VARCHAR(32) | 'pending' | 审核流程状态 |
| confidence_score | DECIMAL(5,2) | NULL | 置信度（%） |
| audit_time | DATETIME | NULL | 审核时间 |
| auditor | VARCHAR(64) | 'AI系统' | 审核员 |

#### 人工审核结果（从接口同步）
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| manual_status | VARCHAR(32) | NULL | 人工处理状态（支付成功/拒绝/补件等） |
| manual_conclusion | TEXT | NULL | 人工审核结论文本 |

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
| is_additional | CHAR(1) | 'N' | 是否需要补件（Y/N） |
| supplementary_count | INT | 0 | 补件次数 |
| supplementary_reason | TEXT | NULL | 补件原因 |
| supplementary_deadline | DATETIME | NULL | 补件截止时间 |

#### 审核结论
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| remark | VARCHAR(2000) | '' | 审核备注（对外展示的结论文本） |
| key_conclusions | LONGTEXT | NULL | 各核对点结论（JSON 数组） |
| decision_reason | TEXT | NULL | 核赔意见（AI 详细说明） |
| final_decision | VARCHAR(32) | NULL | 最终决定（approve/reject/supplement） |

#### 逻辑校验
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| identity_match | CHAR(1) | NULL | 身份是否匹配（Y/N） |
| threshold_met | CHAR(1) | NULL | 是否达到赔付门槛（Y/N） |
| exclusion_triggered | CHAR(1) | NULL | 是否有免责情形（Y/N） |
| exclusion_reason | VARCHAR(256) | NULL | 免责原因 |

#### 前端推送状态
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| forwarded_to_frontend | TINYINT(1) | 0 | 是否已推送到前端（0/1） |
| forwarded_at | DATETIME | NULL | 推送时间 |
| frontend_response | TEXT | NULL | 前端响应内容 |

#### 原始数据 & 元数据
| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| raw_result | LONGTEXT | NULL | 完整审核结果原始 JSON |
| metadata | TEXT | NULL | 扩展元数据（JSON 字符串） |

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
- KEY idx_is_connecting (is_connecting)
- KEY idx_origin_dest (origin_iata, destination_iata)
- KEY idx_missed_connection (missed_connection)

---

### 3. ai_review_segments（联程航段子表）

与 `ai_review_result` 通过 `forceid` 关联，一条主记录对应多行（每段一行）。直飞案件不写入本表。

| 字段名 | 类型 | 默认值 | 说明 |
|--------|------|---------|------|
| id | BIGINT UNSIGNED | AUTO_INCREMENT | 主键 |
| forceid | VARCHAR(64) | | 关联 ai_review_result.forceid（外键） |
| ticket_no | VARCHAR(64) | NULL | 票号 |
| segment_no | TINYINT | 1 | 航段序号（1起算） |
| flight_no | VARCHAR(32) | NULL | 本段航班号 |
| dep_iata | VARCHAR(8) | NULL | 本段起飞机场IATA |
| arr_iata | VARCHAR(8) | NULL | 本段到达机场IATA |
| origin_iata | VARCHAR(8) | NULL | 全程始发地IATA（冗余，方便按段查询） |
| destination_iata | VARCHAR(8) | NULL | 全程目的地IATA（冗余） |
| planned_dep | DATETIME | NULL | 计划起飞时间（材料/保单） |
| planned_arr | DATETIME | NULL | 计划到达时间（材料/保单） |
| actual_dep | DATETIME | NULL | 飞常准实际起飞 |
| actual_arr | DATETIME | NULL | 飞常准实际到达 |
| delay_min | INT | NULL | 本段延误分钟（actual_dep - planned_dep） |
| avi_status | VARCHAR(32) | NULL | 飞常准航班状态（正常/延误/取消） |
| is_triggered | TINYINT(1) | NULL | 是否触发延误险赔付的那段（1=是） |
| is_connecting | TINYINT(1) | NULL | 是否联程（与主表一致，冗余） |
| missed_connect | TINYINT(1) | NULL | 本段是否因前段延误而误机 |
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |

**索引**：
- KEY idx_seg_forceid (forceid)
- KEY idx_seg_flight_no (flight_no)
- KEY idx_seg_is_triggered (is_triggered)
- KEY idx_seg_dep_iata (dep_iata)
- KEY idx_seg_arr_iata (arr_iata)
- FOREIGN KEY fk_seg_forceid → ai_review_result(forceid) ON DELETE CASCADE

---

### 4. ai_supplementary_records（补件记录表）

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

### 5. ai_scheduler_logs（定时任务日志表）

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
| duration_seconds | INT | NULL | 耗时（秒） |
| created_at | DATETIME | CURRENT_TIMESTAMP | 创建时间 |

**索引**：
- KEY idx_task_type (task_type)
- KEY idx_start_time (start_time)
- KEY idx_status (status)

---

### 6. ai_status_history（状态变更历史表）

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

### 7. ai_claim_info_raw（案件原始下载信息存档表）

下载 `claim_info.json` 时同步写入，用于数据丢失时追溯。`raw_json` 字段保留完整原始内容。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | BIGINT UNSIGNED | 主键 |
| forceid | VARCHAR(64) | 案件唯一ID（唯一键） |
| claim_id | VARCHAR(64) | 理赔单号 (ClaimId) |
| benefit_name | VARCHAR(64) | 险种名称 (BenefitName) |
| applicant_name | VARCHAR(128) | 申请人姓名 |
| insured_name | VARCHAR(128) | 被保险人姓名（来自 samePolicyClaim.Insured_And_Policy） |
| id_type | VARCHAR(32) | 证件类型 (ID_Type) |
| id_number | VARCHAR(64) | 证件号码 (ID_Number) |
| birthday | DATE | 出生日期 |
| gender | VARCHAR(8) | 性别 |
| policy_no | VARCHAR(64) | 保单号 (PolicyNo) |
| insurance_company | VARCHAR(128) | 保险公司 |
| product_name | VARCHAR(128) | 产品名称 |
| plan_name | VARCHAR(128) | 计划名称 |
| effective_date | VARCHAR(32) | 保单生效日期（原始字符串） |
| expiry_date | VARCHAR(32) | 保单到期日期（原始字符串） |
| date_of_insurance | VARCHAR(32) | 投保日期 |
| case_insured_name | VARCHAR(128) | 本案被保险人姓名（camelCase字段） |
| case_policy_no | VARCHAR(64) | 本案保单号 |
| case_insurance_company | VARCHAR(128) | 本案保险公司 |
| case_effective_date | VARCHAR(32) | 本案保单生效 |
| case_expiry_date | VARCHAR(32) | 本案保单到期 |
| case_id_type | VARCHAR(32) | 本案证件类型 |
| case_id_number | VARCHAR(64) | 本案证件号码 |
| insured_amount | DECIMAL(10,2) | 保额 |
| reserved_amount | DECIMAL(10,2) | 核定金额 |
| remaining_coverage | DECIMAL(10,2) | 剩余保额 |
| claim_amount | DECIMAL(10,2) | 申请金额 |
| date_of_accident | DATE | 事故日期 |
| final_status | VARCHAR(64) | 案件状态 |
| description_of_accident | TEXT | 事故经过描述 |
| source_date | VARCHAR(128) | 来源渠道 (Source_Date) |
| raw_json | LONGTEXT | 完整 claim_info.json 原始内容 |
| downloaded_at | DATETIME | 首次下载写入时间 |
| updated_at | DATETIME | 最后更新时间 |

**索引**：
- UNIQUE KEY uk_forceid (forceid)
- KEY idx_claim_id (claim_id)
- KEY idx_policy_no (policy_no)
- KEY idx_insured_name (insured_name)
- KEY idx_benefit_name (benefit_name)
- KEY idx_final_status (final_status)
- KEY idx_date_of_accident (date_of_accident)

---

### 8. v_claim_audit_summary（案件审核汇总视图）

包含字段：案件ID、案件编号、被保险人、证件号、航班号、出发/目的城市、计划/实际起飞时间、延误（分钟）、延误原因、保险公司、保单号、审核结果、赔付金额、币种、需补件、补件原因、审核时间、审核员、流程状态、下载状态、审核状态、创建时间。

### 9. v_audit_statistics（审核统计视图）

包含字段：日期、总审核数、通过数、拒绝数、需补件数、平均延误（分钟）、总赔付金额。

---

## 字段统计

### 按表统计字段数量
- ai_claim_status: **19 个字段**
- ai_review_result: **87 个字段**（核心表，航班延误与行李延误共用）
- ai_review_segments: **19 个字段**（联程航段子表）
- ai_supplementary_records: **13 个字段**
- ai_scheduler_logs: **11 个字段**
- ai_status_history: **8 个字段**
- ai_claim_info_raw: **35 个字段**（含 raw_json 完整备份）

### ai_review_result 相较原始版本新增字段（2026-04 迭代）

| 字段名 | 新增时间 | 说明 |
|--------|---------|------|
| benefit_name | 2026-04 | 险种名称，区分航班延误/行李延误 |
| insured_name | 2026-04 | 被保险人姓名（来自 claim_info） |
| manual_status | 2026-04 | 人工处理状态（从接口同步） |
| manual_conclusion | 2026-04 | 人工审核结论文本 |
| final_decision | 2026-04 | 最终决定（approve/reject/supplement） |
| baggage_receipt_time | 2026-04 | 行李签收时间（行李延误险专属） |
| baggage_delay_hours | 2026-04 | 行李延误小时数（行李延误险专属） |
| has_baggage_delay_proof | 2026-04 | 是否有行李延误证明（行李延误险专属） |
| has_baggage_receipt_proof | 2026-04 | 是否有签收时间证明（行李延误险专属） |
| has_baggage_tag_proof | 2026-04 | 是否有行李牌（行李延误险专属） |
| pir_no | 2026-04 | PIR 报告编号/来源描述（行李延误险专属） |

---

*更新时间: 2026-04-22*
