# 数据库结构报告


## ai_review_result (1317 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| forceid | varchar(64) | NO | None | UNI | 案件唯一ID |
| claim_id | varchar(64) | YES | None |  | ClaimId(与上游一致) |
| claim_type | varchar(32) | YES | None | MUL | 案件类型（flight_delay/baggage_delay） |
| applicant_name | varchar(128) | YES | None | MUL | 申请人姓名（来自claim_info.json的Applicant_Name） |
| benefit_name | varchar(64) | YES | None | MUL | 案件类型 |
| insured_name | varchar(128) | YES | None |  | 被保险人姓名(别名) |
| passenger_id_type | varchar(32) | YES | None |  | 证件类型 |
| passenger_id_number | varchar(64) | YES | None |  | 证件号码 |
| policy_no | varchar(64) | YES | None | MUL | 保单号 |
| insurer | varchar(128) | YES | None | MUL | 保险公司 |
| audit_result | varchar(32) | YES | None | MUL | 审核结果 |
| manual_status | varchar(32) | YES | None |  | 人工处理状态(通过/拒绝/需补齐资料) |
| manual_conclusion | text | YES | None |  | 人工结论(补件原因/拒赔原因/理赔金额) |
| ai_model_version | varchar(32) | YES | None |  | AI模型版本标识 |
| pipeline_version | varchar(32) | YES | None |  | 审核pipeline版本 |
| rule_ids_hit | text | YES | None |  | 命中的规则ID列表（JSON数组） |
| audit_reason_tags | text | YES | None |  | 审核原因结构化标签（JSON数组） |
| human_override | varchar(32) | YES | None |  | 人工是否覆盖AI结论 |
| audit_status | varchar(32) | NO | pending | MUL | 审核状态 |
| confidence_score | decimal(5,2) | YES | None |  | 置信度(%) |
| audit_time | datetime | YES | None | MUL | 审核时间 |
| first_ai_audit_result | varchar(32) | YES | None |  |  |
| first_ai_audit_status | varchar(32) | YES | None |  |  |
| first_ai_audit_time | datetime | YES | None |  |  |
| first_ai_confidence | decimal(5,2) | YES | None |  |  |
| first_ai_manual_status | varchar(32) | YES | None |  |  |
| first_ai_manual_conclusion | text | YES | None |  |  |
| auditor | varchar(64) | YES | AI系统 |  | 审核员 |
| payout_amount | decimal(10,2) | YES | None | MUL | 赔付金额 |
| payout_currency | varchar(8) | YES | CNY |  | 赔付币种 |
| insured_amount | decimal(10,2) | YES | None |  | 保额 |
| remaining_coverage | decimal(10,2) | YES | None |  | 剩余保额 |
| policy_effective_date | date | YES | None |  | 保单生效日期 |
| policy_expiry_date | date | YES | None |  | 保单截止日期 |
| remark | varchar(2000) | NO |  |  | 审核结论摘要 |
| is_additional | char(1) | NO | Y | MUL | Y=需补件, N=最终结论 |
| supplementary_reason | text | YES | None |  | 补件原因 |
| key_conclusions | longtext | YES | None |  | 各核对点结论(JSON) |
| decision_reason | text | YES | None |  | 核赔意见 |
| final_decision | varchar(32) | YES | None |  | 最终决定 |
| identity_match | char(1) | YES | None |  | 身份是否匹配 |
| threshold_met | char(1) | YES | None |  | 是否达到赔付门槛 |
| exclusion_triggered | char(1) | YES | None |  | 是否有免责情形 |
| exclusion_reason | varchar(256) | YES | None |  | 免责原因 |
| forwarded_to_frontend | tinyint(1) | NO | 0 | MUL | 是否已推送到前端 |
| forwarded_at | datetime | YES | None |  | 推送时间 |
| raw_result | longtext | YES | None |  | 完整原始JSON |
| created_at | datetime | NO | CURRENT_TIMESTAMP | MUL |  |
| updated_at | datetime | NO | CURRENT_TIMESTAMP |  |  |

**缺失率** (前5个字符串字段):

- `forceid`: 0.0% (0/1317)
- `claim_id`: 6.0% (79/1317)
- `claim_type`: 0.0% (0/1317)
- `applicant_name`: 4.3% (56/1317)
- `benefit_name`: 9.2% (121/1317)

**唯一值统计**:

- `forwarded_to_frontend`: 1 个唯一值

## ai_review_history (7134 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| forceid | varchar(64) | NO | None | MUL |  |
| claim_id | varchar(64) | YES | None |  |  |
| benefit_name | varchar(64) | YES | None |  |  |
| review_type | enum('ai','manual') | NO | None | MUL |  |
| audit_result | varchar(32) | YES | None |  |  |
| audit_status | varchar(32) | YES | None |  |  |
| confidence_score | decimal(5,2) | YES | None |  |  |
| payout_amount | decimal(10,2) | YES | None |  |  |
| identity_match | char(1) | YES | None |  |  |
| threshold_met | char(1) | YES | None |  |  |
| exclusion_triggered | char(1) | YES | None |  |  |
| manual_status | varchar(32) | YES | None |  |  |
| manual_conclusion | text | YES | None |  |  |
| ai_model_version | varchar(32) | YES | None |  |  |
| pipeline_version | varchar(32) | YES | None |  |  |
| rule_ids_hit | text | YES | None |  |  |
| audit_time | datetime | YES | None | MUL |  |
| snapshot_json | longtext | YES | None |  |  |
| created_at | datetime | NO | CURRENT_TIMESTAMP | MUL |  |
| updated_at | datetime | YES | None |  | 最后更新时间 |

**枚举字段**:

- `review_type`: enum('ai','manual')

**缺失率** (前5个字符串字段):

- `forceid`: 0.0% (0/7134)
- `claim_id`: 8.9% (636/7134)
- `benefit_name`: 34.3% (2449/7134)
- `audit_result`: 1.8% (131/7134)
- `audit_status`: 1.8% (131/7134)

## ai_claim_status (1353 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| claim_id | varchar(64) | NO | None | UNI | 上游案件ID |
| forceid | varchar(64) | NO | None | UNI | 案件唯一ID |
| claim_type | varchar(32) | NO | flight_delay | MUL | 案件类型 |
| current_status | varchar(32) | NO | download_pending | MUL | 当前状态 |
| previous_status | varchar(32) | YES | None |  | 上一状态 |
| status_changed_at | datetime | NO | None |  | 状态变更时间 |
| download_status | varchar(32) | NO | pending | MUL | 下载状态 |
| download_attempts | int(11) | NO | 0 |  | 下载尝试次数 |
| last_download_time | datetime | YES | None |  | 最后下载时间 |
| review_status | varchar(32) | NO | pending | MUL | 审核状态 |
| review_attempts | int(11) | NO | 0 |  | 审核尝试次数 |
| last_review_time | datetime | YES | None |  | 最后审核时间 |
| supplementary_count | int(11) | NO | 0 |  | 补件次数 |
| max_supplementary | int(11) | NO | 3 |  | 最大补件次数 |
| next_check_time | datetime | YES | None | MUL | 下次检查时间 |
| error_message | text | YES | None |  | 错误信息 |
| created_at | datetime | NO | CURRENT_TIMESTAMP |  |  |
| updated_at | datetime | NO | CURRENT_TIMESTAMP |  |  |

**缺失率** (前5个字符串字段):

- `claim_id`: 0.0% (0/1353)
- `forceid`: 0.0% (0/1353)
- `claim_type`: 0.0% (0/1353)
- `current_status`: 0.0% (0/1353)
- `previous_status`: 0.0% (0/1353)

**唯一值统计**:

- `download_attempts`: 1 个唯一值
- `review_attempts`: 12 个唯一值
- `supplementary_count`: 14 个唯一值

## ai_flight_delay_data (935 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| forceid | varchar(64) | NO | None | UNI | 关联主表（唯一键） |
| flight_no | varchar(32) | YES | None | MUL | 原航班号 |
| operating_carrier | varchar(128) | YES | None |  | 承运人 |
| dep_iata | varchar(8) | YES | None |  | 原航班出发IATA |
| arr_iata | varchar(8) | YES | None |  | 原航班到达IATA |
| dep_city | varchar(64) | YES | None |  | 出发城市 |
| arr_city | varchar(64) | YES | None |  | 目的城市 |
| planned_dep_time | datetime | YES | None |  | 原计划起飞（延误计算基准） |
| planned_arr_time | datetime | YES | None |  | 原计划到达 |
| actual_dep_time | datetime | YES | None |  | 实际起飞（飞常准优先） |
| actual_arr_time | datetime | YES | None |  | 实际到达（飞常准优先） |
| alt_dep_time | datetime | YES | None |  | 改签航班实际起飞 |
| alt_arr_time | datetime | YES | None |  | 改签航班实际到达 |
| alt_flight_no | varchar(32) | YES | None | MUL | 改签航班号 |
| alt_dep_iata | varchar(8) | YES | None |  | 改签航班出发IATA |
| alt_arr_iata | varchar(8) | YES | None |  | 改签航班到达IATA |
| avi_status | varchar(32) | YES | None | MUL | 飞常准原航班状态 |
| avi_planned_dep | datetime | YES | None |  | 飞常准：原航班计划起飞 |
| avi_planned_arr | datetime | YES | None |  | 飞常准：原航班计划到达 |
| avi_actual_dep | datetime | YES | None |  | 飞常准：原航班实际起飞 |
| avi_actual_arr | datetime | YES | None |  | 飞常准：原航班实际到达 |
| avi_alt_flight_no | varchar(32) | YES | None |  | 飞常准替代航班号 |
| avi_alt_planned_dep | datetime | YES | None |  | 飞常准：替代航班计划起飞 |
| avi_alt_actual_dep | datetime | YES | None |  | 飞常准：替代航班实际起飞 |
| avi_alt_actual_arr | datetime | YES | None |  | 飞常准：替代航班实际到达 |
| flight_scenario | varchar(32) | YES | None | MUL | direct/connecting/rebooking/multi_rebooking/cancelled_nofly |
| rebooking_count | tinyint(4) | NO | 0 |  | 改签次数 |
| is_connecting | tinyint(1) | YES | None | MUL | 是否联程 |
| total_segments | tinyint(4) | YES | None |  | 联程段数 |
| origin_iata | varchar(8) | YES | None | MUL | 全程出发IATA |
| destination_iata | varchar(8) | YES | None |  | 全程目的地IATA |
| missed_connection | tinyint(1) | YES | None | MUL | 是否接驳失误 |
| delay_duration_minutes | int(11) | YES | None |  | 延误时长（分钟） |
| delay_reason | varchar(128) | YES | None |  | 延误原因 |
| delay_type | varchar(32) | YES | None |  | 延误类型 |
| delay_calc_from | varchar(64) | YES | None |  | 延误起算时间点来源字段名 |
| delay_calc_to | varchar(64) | YES | None |  | 延误终止时间点来源字段名 |

**缺失率** (前5个字符串字段):

- `forceid`: 0.0% (0/935)
- `flight_no`: 5.2% (49/935)
- `operating_carrier`: 7.7% (72/935)
- `dep_iata`: 5.2% (49/935)
- `arr_iata`: 5.2% (49/935)

**唯一值统计**:

- `rebooking_count`: 1 个唯一值
- `is_connecting`: 0 个唯一值
- `total_segments`: 0 个唯一值

## ai_baggage_delay_data (429 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| forceid | varchar(64) | NO | None | UNI | 关联主表（唯一键） |
| first_flight_actual_arr_time | datetime | YES | None | MUL | 延误起算点：首次乘坐航班实际到达时间 |
| baggage_receipt_time | datetime | YES | None |  | 延误终止点：行李实际签收时间 |
| baggage_delay_hours | decimal(5,1) | YES | None |  | 延误小时数，自动计算 |
| baggage_delay_calc_basis | varchar(32) | YES | None |  | 计算依据：receipt/transfer_flight/estimated |
| delay_tier | varchar(16) | YES | None | MUL | 延误档位：6-12h/12-18h/18h+ |
| payout_tier_amount | decimal(10,2) | YES | None |  | 档位对应金额：500/1000/1500 |
| claim_amount | decimal(10,2) | YES | None |  | 申请人索赔金额 |
| final_payout_amount | decimal(10,2) | YES | None |  | 最终赔付（取min(档位金额, 索赔金额, 保额上限)） |
| payout_calibration_reason | varchar(256) | YES | None |  | 金额校准原因 |
| has_baggage_receipt_proof | char(1) | YES | None |  | 是否有签收证明（Y/N） |
| has_baggage_delay_proof | char(1) | YES | None |  | 是否有延误证明（Y/N） |
| has_baggage_tag | char(1) | YES | None |  | 是否有行李牌（Y/N） |
| pir_no | varchar(64) | YES | None |  | PIR不正常行李报告编号 |
| has_pir_report | char(1) | YES | None |  | 是否有PIR报告（Y/N） |

**缺失率** (前5个字符串字段):

- `forceid`: 0.0% (0/429)
- `baggage_delay_calc_basis`: 100.0% (429/429)
- `delay_tier`: 100.0% (429/429)
- `payout_calibration_reason`: 100.0% (429/429)
- `pir_no`: 6.3% (27/429)

## ai_claim_info_raw (1063 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| forceid | varchar(64) | NO | None | UNI |  |
| claim_id | varchar(64) | YES | None | MUL |  |
| benefit_name | varchar(64) | YES | None | MUL |  |
| applicant_name | varchar(128) | YES | None |  |  |
| insured_name | varchar(128) | YES | None | MUL |  |
| id_type | varchar(32) | YES | None |  |  |
| id_number | varchar(64) | YES | None |  |  |
| birthday | date | YES | None |  |  |
| gender | varchar(8) | YES | None |  |  |
| policy_no | varchar(64) | YES | None | MUL |  |
| insurance_company | varchar(128) | YES | None |  |  |
| product_name | varchar(128) | YES | None |  |  |
| plan_name | varchar(128) | YES | None |  |  |
| effective_date | varchar(32) | YES | None |  |  |
| expiry_date | varchar(32) | YES | None |  |  |
| date_of_insurance | varchar(32) | YES | None |  |  |
| case_insured_name | varchar(128) | YES | None |  |  |
| case_policy_no | varchar(64) | YES | None |  |  |
| case_insurance_company | varchar(128) | YES | None |  |  |
| case_effective_date | varchar(32) | YES | None |  |  |
| case_expiry_date | varchar(32) | YES | None |  |  |
| case_id_type | varchar(32) | YES | None |  |  |
| case_id_number | varchar(64) | YES | None |  |  |
| insured_amount | decimal(10,2) | YES | None |  |  |
| reserved_amount | decimal(10,2) | YES | None |  |  |
| remaining_coverage | decimal(10,2) | YES | None |  |  |
| claim_amount | decimal(10,2) | YES | None |  |  |
| date_of_accident | date | YES | None | MUL |  |
| final_status | varchar(64) | YES | None | MUL |  |
| description_of_accident | text | YES | None |  |  |
| source_date | varchar(128) | YES | None |  |  |
| raw_json | longtext | YES | None |  |  |
| downloaded_at | datetime | NO | CURRENT_TIMESTAMP |  |  |
| updated_at | datetime | NO | CURRENT_TIMESTAMP |  |  |

**缺失率** (前5个字符串字段):

- `forceid`: 0.0% (0/1063)
- `claim_id`: 0.0% (0/1063)
- `benefit_name`: 0.0% (0/1063)
- `applicant_name`: 0.2% (2/1063)
- `insured_name`: 100.0% (1063/1063)

## ai_supplementary_records (971 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI |  |
| claim_id | varchar(64) | NO | None | MUL | 案件ID |
| forceid | varchar(64) | NO | None | MUL | 案件唯一ID |
| supplementary_number | int(11) | NO | None |  | 第几次补件 |
| requested_at | datetime | NO | None |  | 补件请求时间 |
| requested_reason | text | NO | None |  | 补件原因 |
| required_materials | text | NO | None |  | 所需材料列表 |
| deadline | datetime | NO | None | MUL | 补件截止时间 |
| completed_at | datetime | YES | None |  | 补件完成时间 |
| completed_materials | text | YES | None |  | 已补材料列表 |
| status | varchar(32) | NO | pending | MUL | 状态 |
| created_at | datetime | NO | CURRENT_TIMESTAMP |  |  |
| updated_at | datetime | NO | CURRENT_TIMESTAMP |  |  |

**缺失率** (前5个字符串字段):

- `claim_id`: 0.0% (0/971)
- `forceid`: 0.0% (0/971)
- `requested_reason`: 0.0% (0/971)
- `required_materials`: 0.0% (0/971)
- `completed_materials`: 100.0% (971/971)

**唯一值统计**:

- `supplementary_number`: 17 个唯一值

## ai_review_segments (35 条)

| 字段 | 类型 | 可空 | 默认 | 键 | 说明 |
|------|------|------|------|------|------|
| id | bigint(20) unsigned | NO | None | PRI | 主键 |
| forceid | varchar(64) | NO | None | MUL | 关联 ai_review_result.forceid |
| ticket_no | varchar(64) | YES | None |  |  |
| segment_no | tinyint(4) | NO | 1 |  |  |
| flight_no | varchar(32) | YES | None | MUL |  |
| dep_iata | varchar(8) | YES | None | MUL |  |
| arr_iata | varchar(8) | YES | None | MUL |  |
| origin_iata | varchar(8) | YES | None |  |  |
| destination_iata | varchar(8) | YES | None |  |  |
| planned_dep | datetime | YES | None |  |  |
| planned_arr | datetime | YES | None |  |  |
| actual_dep | datetime | YES | None |  |  |
| actual_arr | datetime | YES | None |  |  |
| delay_min | int(11) | YES | None |  |  |
| avi_status | varchar(32) | YES | None |  |  |
| is_triggered | tinyint(1) | YES | None | MUL |  |
| is_connecting | tinyint(1) | YES | None |  |  |
| missed_connect | tinyint(1) | YES | None |  |  |
| created_at | datetime | NO | CURRENT_TIMESTAMP |  |  |

**缺失率** (前5个字符串字段):

- `forceid`: 0.0% (0/35)
- `ticket_no`: 100.0% (35/35)
- `flight_no`: 0.0% (0/35)
- `dep_iata`: 2.9% (1/35)
- `arr_iata`: 2.9% (1/35)

**唯一值统计**:

- `segment_no`: 3 个唯一值
- `delay_min`: 12 个唯一值
- `is_triggered`: 2 个唯一值