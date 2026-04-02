-- 航班延误AI审核系统 - 数据库表结构
-- 数据库名: ai
-- 版本: 002 (整合所有审核字段)

-- ============================================
-- 1. 案件状态管理表
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
-- 2. 补件记录表
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
-- 3. 定时任务日志表
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
-- 4. 状态变更历史表
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
-- 5. AI审核结果主表（整合所有审核字段）
-- ============================================
CREATE TABLE IF NOT EXISTS ai_review_result (
    -- 基础信息
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一ID',
    claim_id VARCHAR(64) NULL COMMENT '上游案件ID',

    -- 被保险人信息
    passenger_name VARCHAR(128) NULL COMMENT '被保险人姓名',
    passenger_id_type VARCHAR(32) NULL COMMENT '证件类型',
    passenger_id_number VARCHAR(64) NULL COMMENT '证件号码',

    -- 保单信息
    policy_no VARCHAR(64) NULL COMMENT '保单号',
    insurer VARCHAR(128) NULL COMMENT '保险公司',
    policy_effective_date DATE NULL COMMENT '保单生效日期',
    policy_expiry_date DATE NULL COMMENT '保单截止日期',

    -- 航班信息
    flight_no VARCHAR(32) NULL COMMENT '航班号',
    operating_carrier VARCHAR(128) NULL COMMENT '承运人',
    dep_iata VARCHAR(8) NULL COMMENT '出发地IATA',
    arr_iata VARCHAR(8) NULL COMMENT '目的地IATA',
    dep_city VARCHAR(64) NULL COMMENT '出发城市',
    arr_city VARCHAR(64) NULL COMMENT '目的城市',
    dep_country VARCHAR(32) NULL COMMENT '出发国家',
    arr_country VARCHAR(32) NULL COMMENT '目的国家',

    -- 航班时间
    planned_dep_time DATETIME NULL COMMENT '计划起飞时间',
    actual_dep_time DATETIME NULL COMMENT '实际起飞时间',
    planned_arr_time DATETIME NULL COMMENT '计划到达时间',
    actual_arr_time DATETIME NULL COMMENT '实际到达时间',
    alt_dep_time DATETIME NULL COMMENT '替代航班起飞时间',
    alt_arr_time DATETIME NULL COMMENT '替代航班到达时间',

    -- 延误计算
    delay_duration_minutes INT NULL COMMENT '延误时长(分钟)',
    delay_reason VARCHAR(128) NULL COMMENT '延误原因',
    delay_type VARCHAR(32) NULL COMMENT '延误类型',

    -- 审核结果
    audit_result VARCHAR(32) NULL COMMENT '审核结果: 通过/拒绝/需补件',
    audit_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '审核状态',
    confidence_score DECIMAL(5,2) NULL COMMENT '置信度(%)',
    audit_time DATETIME NULL COMMENT '审核时间',
    auditor VARCHAR(64) NULL DEFAULT 'AI系统' COMMENT '审核员',

    -- 赔付信息
    payout_amount DECIMAL(10,2) NULL COMMENT '赔付金额',
    payout_currency VARCHAR(8) NULL DEFAULT 'CNY' COMMENT '赔付币种',
    payout_basis VARCHAR(256) NULL COMMENT '赔付依据',
    insured_amount DECIMAL(10,2) NULL COMMENT '保额',
    remaining_coverage DECIMAL(10,2) NULL COMMENT '剩余保额',

    -- 补件信息
    is_additional CHAR(1) NOT NULL DEFAULT 'N' COMMENT '是否需要补件: Y/N',
    supplementary_count INT NOT NULL DEFAULT 0 COMMENT '补件次数',
    supplementary_reason TEXT NULL COMMENT '补件原因',
    supplementary_deadline DATETIME NULL COMMENT '补件截止时间',

    -- 审核结论
    remark VARCHAR(2000) NULL COMMENT '审核备注',
    key_conclusions LONGTEXT NULL COMMENT '各核对点结论(JSON)',
    decision_reason TEXT NULL COMMENT '核赔意见',

    -- 逻辑校验
    identity_match CHAR(1) NULL COMMENT '身份是否匹配',
    threshold_met CHAR(1) NULL COMMENT '是否达到赔付门槛',
    exclusion_triggered CHAR(1) NULL COMMENT '是否有免责情形',
    exclusion_reason VARCHAR(256) NULL COMMENT '免责原因',

    -- 前端推送状态
    forwarded_to_frontend BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否已推送到前端',
    forwarded_at DATETIME NULL COMMENT '推送时间',
    frontend_response TEXT NULL COMMENT '前端响应',

    -- 原始数据
    raw_result LONGTEXT NULL COMMENT '完整原始JSON',

    -- 元数据
    metadata JSON NULL COMMENT '扩展元数据',

    -- 时间戳
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    UNIQUE KEY uk_forceid (forceid),
    KEY idx_claim_id (claim_id),
    KEY idx_audit_result (audit_result),
    KEY idx_audit_status (audit_status),
    KEY idx_is_additional (is_additional),
    KEY idx_passenger_name (passenger_name),
    KEY idx_flight_no (flight_no),
    KEY idx_policy_no (policy_no),
    KEY idx_insurer (insurer),
    KEY idx_delay_duration (delay_duration_minutes),
    KEY idx_payout_amount (payout_amount),
    KEY idx_audit_time (audit_time),
    KEY idx_forwarded_to_frontend (forwarded_to_frontend),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI理赔审核结果主表';

-- ============================================
-- 6. 创建视图方便查询
-- ============================================
CREATE OR REPLACE VIEW v_claim_audit_summary AS
SELECT
    r.forceid AS '案件ID',
    r.claim_id AS '案件编号',
    r.passenger_name AS '被保险人',
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
-- 7. 创建统计视图
-- ============================================
CREATE OR REPLACE VIEW v_audit_statistics AS
SELECT
    DATE(audit_time) AS '日期',
    COUNT(*) AS '总审核数',
    SUM(CASE WHEN audit_result = '通过' THEN 1 ELSE 0 END) AS '通过数',
    SUM(CASE WHEN audit_result = '拒绝' THEN 1 ELSE 0 END) AS '拒绝数',
    SUM(CASE WHEN is_additional = 'Y' THEN 1 ELSE 0 END) AS '需补件数',
    ROUND(AVG(delay_duration_minutes), 0) AS '平均延误(分钟)',
    SUM(payout_amount) AS '总赔付金额'
FROM ai_review_result
WHERE audit_time IS NOT NULL
GROUP BY DATE(audit_time)
ORDER BY DATE(audit_time) DESC;

-- ============================================
-- 迁移完成
-- ============================================
SELECT 'Database tables created successfully' AS status;