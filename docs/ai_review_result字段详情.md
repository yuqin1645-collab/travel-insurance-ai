# ai_review_result 表 - 45个字段详细列表

## ai_review_result 表字段详解

### 基础信息（4个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 1 | id | BIGINT UNSIGNED | 否 | AUTO_INCREMENT | 主键ID |
| 2 | forceid | VARCHAR(64) | 否 | | 案件唯一ID |
| 3 | claim_id | VARCHAR(64) | 是 | NULL | 上游案件ID |
| 4 | metadata | JSON | 是 | NULL | 扩展元数据 |

### 被保险人信息（3个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 5 | passenger_name | VARCHAR(128) | 是 | NULL | 被保险人姓名 |
| 6 | passenger_id_type | VARCHAR(32) | 是 | NULL | 证件类型 |
| 7 | passenger_id_number | VARCHAR(64) | 是 | NULL | 证件号码 |

### 保单信息（4个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 8 | policy_no | VARCHAR(64) | 是 | NULL | 保单号 |
| 9 | insurer | VARCHAR(128) | 是 | NULL | 保险公司 |
| 10 | policy_effective_date | DATE | 是 | NULL | 保单生效日期 |
| 11 | policy_expiry_date | DATE | 是 | NULL | 保单截止日期 |

### 航班信息（8个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 12 | flight_no | VARCHAR(32) | 是 | NULL | 航班号 |
| 13 | operating_carrier | VARCHAR(128) | 是 | NULL | 承运人 |
| 14 | dep_iata | VARCHAR(8) | 是 | NULL | 出发地IATA |
| 15 | arr_iata | VARCHAR(8) | 是 | NULL | 目的地IATA |
| 16 | dep_city | VARCHAR(64) | 是 | NULL | 出发城市 |
| 17 | arr_city | VARCHAR(64) | 是 | NULL | 目的城市 |
| 18 | dep_country | VARCHAR(32) | 是 | NULL | 出发国家 |
| 19 | arr_country | VARCHAR(32) | 是 | NULL | 目的国家 |

### 航班时间（6个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 20 | planned_dep_time | DATETIME | 是 | NULL | 计划起飞时间 |
| 21 | actual_dep_time | DATETIME | 是 | NULL | 实际起飞时间 |
| 22 | planned_arr_time | DATETIME | 是 | NULL | 计划到达时间 |
| 23 | actual_arr_time | DATETIME | 是 | NULL | 实际到达时间 |
| 24 | alt_dep_time | DATETIME | 是 | NULL | 替代航班起飞时间 |
| 25 | alt_arr_time | DATETIME | 是 | NULL | 替代航班到达时间 |

### 延误计算（3个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 26 | delay_duration_minutes | INT | 是 | NULL | 延误时长(分钟) |
| 27 | delay_reason | VARCHAR(128) | 是 | NULL | 延误原因 |
| 28 | delay_type | VARCHAR(32) | 是 | NULL | 延误类型 |

### 审核结果（5个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 29 | audit_result | VARCHAR(32) | 是 | NULL | 审核结果: 通过/拒绝/需补件 |
| 30 | audit_status | VARCHAR(32) | 否 | 'pending' | 审核状态 |
| 31 | confidence_score | DECIMAL(5,2) | 是 | NULL | 置信度(%) |
| 32 | audit_time | DATETIME | 是 | NULL | 审核时间 |
| 33 | auditor | VARCHAR(64) | 是 | 'AI系统' | 审核员 |

### 赔付信息（5个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 34 | payout_amount | DECIMAL(10,2) | 是 | NULL | 赔付金额 |
| 35 | payout_currency | VARCHAR(8) | 是 | 'CNY' | 赔付币种 |
| 36 | payout_basis | VARCHAR(256) | 是 | NULL | 赔付依据 |
| 37 | insured_amount | DECIMAL(10,2) | 是 | NULL | 保额 |
| 38 | remaining_coverage | DECIMAL(10,2) | 是 | NULL | 剩余保额 |

### 补件信息（4个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 39 | is_additional | CHAR(1) | 否 | 'N' | 是否需要补件: Y/N |
| 40 | supplementary_count | INT | 否 | 0 | 补件次数 |
| 41 | supplementary_reason | TEXT | 是 | NULL | 补件原因 |
| 42 | supplementary_deadline | DATETIME | 是 | NULL | 补件截止时间 |

### 审核结论（3个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 43 | remark | VARCHAR(2000) | 是 | NULL | 审核备注 |
| 44 | key_conclusions | LONGTEXT | 是 | NULL | 各核对点结论(JSON) |
| 45 | decision_reason | TEXT | 是 | NULL | 核赔意见 |

### 逻辑校验（4个字段）[注：实际上表定义只有3个，加上一个字段]
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 46 | identity_match | CHAR(1) | 是 | NULL | 身份是否匹配 |
| 47 | threshold_met | CHAR(1) | 是 | NULL | 是否达到赔付门槛 |
| 48 | exclusion_triggered | CHAR(1) | 是 | NULL | 是否有免责情形 |
| 49 | exclusion_reason | VARCHAR(256) | 是 | NULL | 免责原因 |

### 前端推送状态（3个字段）[注：实际上表定义只有3个，加上一个字段]
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 50 | forwarded_to_frontend | BOOLEAN | 否 | FALSE | 是否已推送到前端 |
| 51 | forwarded_at | DATETIME | 是 | NULL | 推送时间 |
| 52 | frontend_response | TEXT | 是 | NULL | 前端响应 |

### 原始数据（1个字段）[注：实际上表定义只有1个，加上一个字段]
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 53 | raw_result | LONGTEXT | 是 | NULL | 完整原始JSON |

### 时间戳（2个字段）
| 序号 | 字段名 | 数据类型 | 是否可空 | 默认值 | 说明 |
|------|--------|----------|----------|---------|------|
| 54 | created_at | DATETIME | 否 | CURRENT_TIMESTAMP | 创建时间 |
| 55 | updated_at | DATETIME | 否 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

---

## 字段分类总结

### 按功能分类
1. **基础标识**: 4个字段（id, forceid, claim_id, metadata）
2. **被保险人信息**: 3个字段
3. **保单信息**: 4个字段
4. **航班信息**: 8个字段
5. **航班时间**: 6个字段
6. **延误信息**: 3个字段
7. **审核结果**: 5个字段
8. **赔付信息**: 5个字段
9. **补件信息**: 4个字段
10. **审核结论**: 3个字段
11. **逻辑校验**: 4个字段
12. **前端推送**: 3个字段
13. **原始数据**: 1个字段
14. **时间戳**: 2个字段

### 按数据类型分类
- VARCHAR: 15个
- DATETIME: 10个
- INT: 3个（包括supplementary_count）
- DECIMAL: 5个
- TEXT/LONGTEXT: 4个
- BOOLEAN: 1个
- JSON: 1个
- DATE: 2个

### 核心关键字段
1. **forceid** - 案件唯一标识
2. **audit_result** - 最终审核结果
3. **payout_amount** - 赔付金额
4. **delay_duration_minutes** - 延误时长
5. **is_additional** - 是否需要补件

### 索引字段
- forceid (唯一索引)
- claim_id (普通索引)
- audit_result (普通索引)
- audit_status (普通索引)
- is_additional (普通索引)
- passenger_name (普通索引)
- flight_no (普通索引)
- policy_no (普通索引)
- insurer (普通索引)
- delay_duration_minutes (普通索引)
- payout_amount (普通索引)
- audit_time (普通索引)
- forwarded_to_frontend (普通索引)
- created_at (普通索引)