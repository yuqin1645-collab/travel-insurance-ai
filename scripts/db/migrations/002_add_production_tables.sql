-- 航班延误AI审核系统 - 生产化数据库迁移
-- 只添加新表/字段，不影响现有数据
-- 版本: 003

-- ============================================
-- 1. 扩展 ai_review_result 表（添加缺失字段）
-- ============================================
ALTER TABLE ai_review_result
    ADD COLUMN IF NOT EXISTS applicant_name VARCHAR(128) NULL COMMENT '申请人姓名（来自claim_info.json的Applicant_Name）' AFTER claim_id,
    ADD COLUMN IF NOT EXISTS passenger_id_type VARCHAR(32) NULL COMMENT '证件类型' AFTER applicant_name,
    ADD COLUMN IF NOT EXISTS passenger_id_number VARCHAR(64) NULL COMMENT '证件号码' AFTER passenger_id_type,
    ADD COLUMN IF NOT EXISTS policy_no VARCHAR(64) NULL COMMENT '保单号' AFTER passenger_id_number,
    ADD COLUMN IF NOT EXISTS insurer VARCHAR(128) NULL COMMENT '保险公司' AFTER policy_no,
    ADD COLUMN IF NOT EXISTS policy_effective_date DATE NULL COMMENT '保单生效日期' AFTER insurer,
    ADD COLUMN IF NOT EXISTS policy_expiry_date DATE NULL COMMENT '保单截止日期' AFTER policy_effective_date,
    ADD COLUMN IF NOT EXISTS operating_carrier VARCHAR(128) NULL COMMENT '承运人' AFTER flight_no,
    ADD COLUMN IF NOT EXISTS dep_city VARCHAR(64) NULL COMMENT '出发城市' AFTER arr_iata,
    ADD COLUMN IF NOT EXISTS arr_city VARCHAR(64) NULL COMMENT '目的城市' AFTER dep_city,
    ADD COLUMN IF NOT EXISTS dep_country VARCHAR(32) NULL COMMENT '出发国家' AFTER arr_city,
    ADD COLUMN IF NOT EXISTS arr_country VARCHAR(32) NULL COMMENT '目的国家' AFTER dep_country,
    ADD COLUMN IF NOT EXISTS planned_dep_time DATETIME NULL COMMENT '计划起飞时间' AFTER arr_country,
    ADD COLUMN IF NOT EXISTS actual_dep_time DATETIME NULL COMMENT '实际起飞时间' AFTER planned_dep_time,
    ADD COLUMN IF NOT EXISTS planned_arr_time DATETIME NULL COMMENT '计划到达时间' AFTER actual_dep_time,
    ADD COLUMN IF NOT EXISTS actual_arr_time DATETIME NULL COMMENT '实际到达时间' AFTER planned_arr_time,
    ADD COLUMN IF NOT EXISTS alt_dep_time DATETIME NULL COMMENT '替代航班起飞时间' AFTER actual_arr_time,
    ADD COLUMN IF NOT EXISTS alt_arr_time DATETIME NULL COMMENT '替代航班到达时间' AFTER alt_dep_time,
    ADD COLUMN IF NOT EXISTS delay_duration_minutes INT NULL COMMENT '延误时长(分钟)' AFTER alt_arr_time,
    ADD COLUMN IF NOT EXISTS delay_reason VARCHAR(128) NULL COMMENT '延误原因' AFTER delay_duration_minutes,
    ADD COLUMN IF NOT EXISTS delay_type VARCHAR(32) NULL COMMENT '延误类型' AFTER delay_reason,
    ADD COLUMN IF NOT EXISTS audit_result VARCHAR(32) NULL COMMENT '审核结果: 通过/拒绝/需补件' AFTER delay_type,
    ADD COLUMN IF NOT EXISTS audit_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '审核状态' AFTER audit_result,
    ADD COLUMN IF NOT EXISTS confidence_score DECIMAL(5,2) NULL COMMENT '置信度(%)' AFTER audit_status,
    ADD COLUMN IF NOT EXISTS audit_time DATETIME NULL COMMENT '审核时间' AFTER confidence_score,
    ADD COLUMN IF NOT EXISTS auditor VARCHAR(64) NULL DEFAULT 'AI系统' COMMENT '审核员' AFTER audit_time,
    ADD COLUMN IF NOT EXISTS payout_amount DECIMAL(10,2) NULL COMMENT '赔付金额' AFTER auditor,
    ADD COLUMN IF NOT EXISTS payout_currency VARCHAR(8) NULL DEFAULT 'CNY' COMMENT '赔付币种' AFTER payout_amount,
    ADD COLUMN IF NOT EXISTS payout_basis VARCHAR(256) NULL COMMENT '赔付依据' AFTER payout_currency,
    ADD COLUMN IF NOT EXISTS insured_amount DECIMAL(10,2) NULL COMMENT '保额' AFTER payout_basis,
    ADD COLUMN IF NOT EXISTS remaining_coverage DECIMAL(10,2) NULL COMMENT '剩余保额' AFTER insured_amount,
    ADD COLUMN IF NOT EXISTS supplementary_count INT NOT NULL DEFAULT 0 COMMENT '补件次数' AFTER is_additional,
    ADD COLUMN IF NOT EXISTS supplementary_reason TEXT NULL COMMENT '补件原因' AFTER supplementary_count,
    ADD COLUMN IF NOT EXISTS supplementary_deadline DATETIME NULL COMMENT '补件截止时间' AFTER supplementary_reason,
    ADD COLUMN IF NOT EXISTS decision_reason TEXT NULL COMMENT '核赔意见' AFTER key_conclusions,
    ADD COLUMN IF NOT EXISTS identity_match CHAR(1) NULL COMMENT '身份是否匹配' AFTER decision_reason,
    ADD COLUMN IF NOT EXISTS threshold_met CHAR(1) NULL COMMENT '是否达到赔付门槛' AFTER identity_match,
    ADD COLUMN IF NOT EXISTS exclusion_triggered CHAR(1) NULL COMMENT '是否有免责情形' AFTER threshold_met,
    ADD COLUMN IF NOT EXISTS exclusion_reason VARCHAR(256) NULL COMMENT '免责原因' AFTER exclusion_triggered,
    ADD COLUMN IF NOT EXISTS forwarded_to_frontend BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否已推送到前端' AFTER exclusion_reason,
    ADD COLUMN IF NOT EXISTS forwarded_at DATETIME NULL COMMENT '推送时间' AFTER forwarded_to_frontend,
    ADD COLUMN IF NOT EXISTS frontend_response TEXT NULL COMMENT '前端响应' AFTER forwarded_at,
    ADD COLUMN IF NOT EXISTS metadata JSON NULL COMMENT '扩展元数据' AFTER raw_result;

-- 添加索引（忽略已存在的错误）
ALTER TABLE ai_review_result ADD INDEX idx_audit_result (audit_result);
ALTER TABLE ai_review_result ADD INDEX idx_audit_status (audit_status);
ALTER TABLE ai_review_result ADD INDEX idx_is_additional (is_additional);
ALTER TABLE ai_review_result ADD INDEX idx_applicant_name (applicant_name);
ALTER TABLE ai_review_result ADD INDEX idx_flight_no (flight_no);
ALTER TABLE ai_review_result ADD INDEX idx_policy_no (policy_no);
ALTER TABLE ai_review_result ADD INDEX idx_insurer (insurer);
ALTER TABLE ai_review_result ADD INDEX idx_delay_duration (delay_duration_minutes);
ALTER TABLE ai_review_result ADD INDEX idx_payout_amount (payout_amount);
ALTER TABLE ai_review_result ADD INDEX idx_audit_time (audit_time);
ALTER TABLE ai_review_result ADD INDEX idx_forwarded_to_frontend (forwarded_to_frontend);

-- ============================================
-- 2. 创建案件状态管理表
-- ============================================
CREATE TABLE IF NOT EXISTS ai_claim_status (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    claim_id VARCHAR(64) NOT NULL COMMENT '上游案件ID',
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一ID',
    claim_type VARCHAR(32) NOT NULL DEFAULT 'flight_delay' COMMENT '案件类型',
    current_status VARCHAR(32) NOT NULL DEFAULT 'download_pending' COMMENT '当前状态',
    previous_status VARCHAR(32) NULL COMMENT '上一状态',
    status_changed_at DATETIME NOT NULL COMMENT '状态变更时间',
    download_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '下载状态',
    download_attempts INT NOT NULL DEFAULT 0 COMMENT '下载尝试次数',
    last_download_time DATETIME NULL COMMENT '最后下载时间',
    review_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '审核状态',
    review_attempts INT NOT NULL DEFAULT 0 COMMENT '审核尝试次数',
    last_review_time DATETIME NULL COMMENT '最后审核时间',
    supplementary_count INT NOT NULL DEFAULT 0 COMMENT '补件次数',
    max_supplementary INT NOT NULL DEFAULT 3 COMMENT '最大补件次数',
    next_check_time DATETIME NULL COMMENT '下次检查时间',
    error_message TEXT NULL COMMENT '错误信息',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_forceid (forceid),
    UNIQUE KEY uk_claim_id (claim_id),
    KEY idx_current_status (current_status),
    KEY idx_download_status (download_status),
    KEY idx_review_status (review_status),
    KEY idx_next_check_time (next_check_time),
    KEY idx_claim_type (claim_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='案件状态管理表';

-- ============================================
-- 3. 创建补件记录表
-- ============================================
CREATE TABLE IF NOT EXISTS ai_supplementary_records (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    claim_id VARCHAR(64) NOT NULL COMMENT '案件ID',
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一ID',
    supplementary_number INT NOT NULL COMMENT '第几次补件',
    requested_at DATETIME NOT NULL COMMENT '补件请求时间',
    requested_reason TEXT NOT NULL COMMENT '补件原因',
    required_materials JSON NOT NULL COMMENT '所需材料列表',
    deadline DATETIME NOT NULL COMMENT '补件截止时间',
    completed_at DATETIME NULL COMMENT '补件完成时间',
    completed_materials JSON NULL COMMENT '已补材料列表',
    status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '状态',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_claim_id (claim_id),
    KEY idx_forceid (forceid),
    KEY idx_status (status),
    KEY idx_deadline (deadline)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='补件记录表';

-- ============================================
-- 4. 创建定时任务日志表
-- ============================================
CREATE TABLE IF NOT EXISTS ai_scheduler_logs (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_type VARCHAR(32) NOT NULL COMMENT '任务类型',
    start_time DATETIME NOT NULL COMMENT '开始时间',
    end_time DATETIME NULL COMMENT '结束时间',
    status VARCHAR(32) NOT NULL COMMENT '状态',
    processed_count INT NOT NULL DEFAULT 0 COMMENT '处理数量',
    success_count INT NOT NULL DEFAULT 0 COMMENT '成功数量',
    failed_count INT NOT NULL DEFAULT 0 COMMENT '失败数量',
    error_message TEXT NULL COMMENT '错误信息',
    duration_seconds INT NULL COMMENT '耗时(秒)',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_task_type (task_type),
    KEY idx_start_time (start_time),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='定时任务日志表';

-- ============================================
-- 5. 创建状态变更历史表
-- ============================================
CREATE TABLE IF NOT EXISTS ai_status_history (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    claim_id VARCHAR(64) NOT NULL COMMENT '案件ID',
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一ID',
    from_status VARCHAR(32) NULL COMMENT '原状态',
    to_status VARCHAR(32) NOT NULL COMMENT '新状态',
    changed_by VARCHAR(64) NOT NULL DEFAULT 'system' COMMENT '变更者',
    change_reason TEXT NULL COMMENT '变更原因',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_claim_id (claim_id),
    KEY idx_forceid (forceid),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='状态变更历史表';

-- ============================================
-- 6. 创建视图方便查询
-- ============================================
CREATE OR REPLACE VIEW v_claim_audit_summary AS
SELECT
    r.forceid AS '案件ID',
    r.claim_id AS '案件编号',
    r.applicant_name AS '申请人',
    r.passenger_id_number AS '证件号',
    r.flight_no AS '航班号',
    r.dep_city AS '出发城市',
    r.arr_city AS '目的城市',
    r.planned_dep_time AS '计划起飞',
    r.actual_dep_time AS '实际起飞',
    r.delay_duration_minutes AS '延误(分钟)',
    r.delay_reason AS '延误原因',
    r.insurer AS '保险公司',
    r.policy_no AS '保单号',
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额',
    r.payout_currency AS '币种',
    r.is_additional AS '需补件',
    r.supplementary_reason AS '补件原因',
    r.audit_time AS '审核时间',
    r.auditor AS '审核员',
    s.current_status AS '流程状态',
    s.download_status AS '下载状态',
    s.review_status AS '审核状态',
    r.created_at AS '创建时间'
FROM ai_review_result r
LEFT JOIN ai_claim_status s ON r.forceid = s.forceid;

-- ============================================
-- 迁移完成
-- ============================================
SELECT 'Migration completed successfully' AS status;
