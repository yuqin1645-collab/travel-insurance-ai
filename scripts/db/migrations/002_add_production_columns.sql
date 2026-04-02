-- 航班延误AI审核系统 - 生产化数据库迁移
-- 只添加新表/字段，不影响现有数据
-- 版本: 003 (MySQL兼容版)

-- ============================================
-- 1. 扩展 ai_review_result 表（逐列添加）
-- ============================================

-- 被保险人信息
ALTER TABLE ai_review_result ADD COLUMN passenger_name VARCHAR(128) NULL COMMENT '被保险人姓名' AFTER claim_id;
ALTER TABLE ai_review_result ADD COLUMN passenger_id_type VARCHAR(32) NULL COMMENT '证件类型' AFTER passenger_name;
ALTER TABLE ai_review_result ADD COLUMN passenger_id_number VARCHAR(64) NULL COMMENT '证件号码' AFTER passenger_id_type;

-- 保单信息
ALTER TABLE ai_review_result ADD COLUMN policy_no VARCHAR(64) NULL COMMENT '保单号' AFTER passenger_id_number;
ALTER TABLE ai_review_result ADD COLUMN insurer VARCHAR(128) NULL COMMENT '保险公司' AFTER policy_no;
ALTER TABLE ai_review_result ADD COLUMN policy_effective_date DATE NULL COMMENT '保单生效日期' AFTER insurer;
ALTER TABLE ai_review_result ADD COLUMN policy_expiry_date DATE NULL COMMENT '保单截止日期' AFTER policy_effective_date;

-- 航班信息
ALTER TABLE ai_review_result ADD COLUMN operating_carrier VARCHAR(128) NULL COMMENT '承运人' AFTER flight_no;
ALTER TABLE ai_review_result ADD COLUMN dep_city VARCHAR(64) NULL COMMENT '出发城市' AFTER arr_iata;
ALTER TABLE ai_review_result ADD COLUMN arr_city VARCHAR(64) NULL COMMENT '目的城市' AFTER dep_city;
ALTER TABLE ai_review_result ADD COLUMN dep_country VARCHAR(32) NULL COMMENT '出发国家' AFTER arr_city;
ALTER TABLE ai_review_result ADD COLUMN arr_country VARCHAR(32) NULL COMMENT '目的国家' AFTER dep_country;

-- 航班时间
ALTER TABLE ai_review_result ADD COLUMN planned_dep_time DATETIME NULL COMMENT '计划起飞时间' AFTER arr_country;
ALTER TABLE ai_review_result ADD COLUMN actual_dep_time DATETIME NULL COMMENT '实际起飞时间' AFTER planned_dep_time;
ALTER TABLE ai_review_result ADD COLUMN planned_arr_time DATETIME NULL COMMENT '计划到达时间' AFTER actual_dep_time;
ALTER TABLE ai_review_result ADD COLUMN actual_arr_time DATETIME NULL COMMENT '实际到达时间' AFTER planned_arr_time;
ALTER TABLE ai_review_result ADD COLUMN alt_dep_time DATETIME NULL COMMENT '替代航班起飞时间' AFTER actual_arr_time;
ALTER TABLE ai_review_result ADD COLUMN alt_arr_time DATETIME NULL COMMENT '替代航班到达时间' AFTER alt_dep_time;

-- 延误计算
ALTER TABLE ai_review_result ADD COLUMN delay_duration_minutes INT NULL COMMENT '延误时长(分钟)' AFTER alt_arr_time;
ALTER TABLE ai_review_result ADD COLUMN delay_reason VARCHAR(128) NULL COMMENT '延误原因' AFTER delay_duration_minutes;
ALTER TABLE ai_review_result ADD COLUMN delay_type VARCHAR(32) NULL COMMENT '延误类型' AFTER delay_reason;

-- 审核结果
ALTER TABLE ai_review_result ADD COLUMN audit_result VARCHAR(32) NULL COMMENT '审核结果' AFTER delay_type;
ALTER TABLE ai_review_result ADD COLUMN audit_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '审核状态' AFTER audit_result;
ALTER TABLE ai_review_result ADD COLUMN confidence_score DECIMAL(5,2) NULL COMMENT '置信度(%)' AFTER audit_status;
ALTER TABLE ai_review_result ADD COLUMN audit_time DATETIME NULL COMMENT '审核时间' AFTER confidence_score;
ALTER TABLE ai_review_result ADD COLUMN auditor VARCHAR(64) NULL DEFAULT 'AI系统' COMMENT '审核员' AFTER audit_time;

-- 赔付信息
ALTER TABLE ai_review_result ADD COLUMN payout_amount DECIMAL(10,2) NULL COMMENT '赔付金额' AFTER auditor;
ALTER TABLE ai_review_result ADD COLUMN payout_currency VARCHAR(8) NULL DEFAULT 'CNY' COMMENT '赔付币种' AFTER payout_amount;
ALTER TABLE ai_review_result ADD COLUMN payout_basis VARCHAR(256) NULL COMMENT '赔付依据' AFTER payout_currency;
ALTER TABLE ai_review_result ADD COLUMN insured_amount DECIMAL(10,2) NULL COMMENT '保额' AFTER payout_basis;
ALTER TABLE ai_review_result ADD COLUMN remaining_coverage DECIMAL(10,2) NULL COMMENT '剩余保额' AFTER insured_amount;

-- 补件信息
ALTER TABLE ai_review_result ADD COLUMN supplementary_count INT NOT NULL DEFAULT 0 COMMENT '补件次数' AFTER is_additional;
ALTER TABLE ai_review_result ADD COLUMN supplementary_reason TEXT NULL COMMENT '补件原因' AFTER supplementary_count;
ALTER TABLE ai_review_result ADD COLUMN supplementary_deadline DATETIME NULL COMMENT '补件截止时间' AFTER supplementary_reason;

-- 审核结论
ALTER TABLE ai_review_result ADD COLUMN decision_reason TEXT NULL COMMENT '核赔意见' AFTER key_conclusions;

-- 逻辑校验
ALTER TABLE ai_review_result ADD COLUMN identity_match CHAR(1) NULL COMMENT '身份是否匹配' AFTER decision_reason;
ALTER TABLE ai_review_result ADD COLUMN threshold_met CHAR(1) NULL COMMENT '是否达到赔付门槛' AFTER identity_match;
ALTER TABLE ai_review_result ADD COLUMN exclusion_triggered CHAR(1) NULL COMMENT '是否有免责情形' AFTER threshold_met;
ALTER TABLE ai_review_result ADD COLUMN exclusion_reason VARCHAR(256) NULL COMMENT '免责原因' AFTER exclusion_triggered;

-- 前端推送状态
ALTER TABLE ai_review_result ADD COLUMN forwarded_to_frontend BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否已推送到前端' AFTER exclusion_reason;
ALTER TABLE ai_review_result ADD COLUMN forwarded_at DATETIME NULL COMMENT '推送时间' AFTER forwarded_to_frontend;
ALTER TABLE ai_review_result ADD COLUMN frontend_response TEXT NULL COMMENT '前端响应' AFTER forwarded_at;

-- 元数据
ALTER TABLE ai_review_result ADD COLUMN metadata JSON NULL COMMENT '扩展元数据' AFTER raw_result;

-- 添加索引
ALTER TABLE ai_review_result ADD INDEX idx_audit_result (audit_result);
ALTER TABLE ai_review_result ADD INDEX idx_audit_status (audit_status);
ALTER TABLE ai_review_result ADD INDEX idx_is_additional (is_additional);
ALTER TABLE ai_review_result ADD INDEX idx_passenger_name (passenger_name);
ALTER TABLE ai_review_result ADD INDEX idx_flight_no (flight_no);
ALTER TABLE ai_review_result ADD INDEX idx_policy_no (policy_no);
ALTER TABLE ai_review_result ADD INDEX idx_insurer (insurer);
ALTER TABLE ai_review_result ADD INDEX idx_delay_duration (delay_duration_minutes);
ALTER TABLE ai_review_result ADD INDEX idx_payout_amount (payout_amount);
ALTER TABLE ai_review_result ADD INDEX idx_audit_time (audit_time);
ALTER TABLE ai_review_result ADD INDEX idx_forwarded_to_frontend (forwarded_to_frontend);

SELECT 'Migration completed - ai_review_result extended' AS status;
