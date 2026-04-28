-- 数据库结构改进 - 第1阶段：新建子表 + 主表新增列
-- 版本: 007
-- 说明: 只添加新表/新列，不删除或修改现有数据，可安全回滚

-- ============================================
-- Step 1: 新建 ai_flight_delay_data 表
-- ============================================
CREATE TABLE IF NOT EXISTS ai_flight_delay_data (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '关联主表（唯一键）',

    -- 航班基础信息
    flight_no VARCHAR(32) NULL COMMENT '原航班号',
    operating_carrier VARCHAR(128) NULL COMMENT '承运人',
    dep_iata VARCHAR(8) NULL COMMENT '原航班出发IATA',
    arr_iata VARCHAR(8) NULL COMMENT '原航班到达IATA',
    dep_city VARCHAR(64) NULL COMMENT '出发城市',
    arr_city VARCHAR(64) NULL COMMENT '目的城市',

    -- 原航班时间
    planned_dep_time DATETIME NULL COMMENT '原计划起飞（延误计算基准）',
    planned_arr_time DATETIME NULL COMMENT '原计划到达',
    actual_dep_time DATETIME NULL COMMENT '实际起飞（飞常准优先）',
    actual_arr_time DATETIME NULL COMMENT '实际到达（飞常准优先）',

    -- 实际乘坐航班（改签/替代）
    alt_dep_time DATETIME NULL COMMENT '改签航班实际起飞',
    alt_arr_time DATETIME NULL COMMENT '改签航班实际到达',
    alt_flight_no VARCHAR(32) NULL COMMENT '改签航班号',
    alt_dep_iata VARCHAR(8) NULL COMMENT '改签航班出发IATA',
    alt_arr_iata VARCHAR(8) NULL COMMENT '改签航班到达IATA',

    -- 飞常准原航班独立字段
    avi_status VARCHAR(32) NULL COMMENT '飞常准原航班状态',
    avi_planned_dep DATETIME NULL COMMENT '飞常准：原航班计划起飞',
    avi_planned_arr DATETIME NULL COMMENT '飞常准：原航班计划到达',
    avi_actual_dep DATETIME NULL COMMENT '飞常准：原航班实际起飞',
    avi_actual_arr DATETIME NULL COMMENT '飞常准：原航班实际到达',

    -- 飞常准替代航班独立字段
    avi_alt_flight_no VARCHAR(32) NULL COMMENT '飞常准替代航班号',
    avi_alt_planned_dep DATETIME NULL COMMENT '飞常准：替代航班计划起飞',
    avi_alt_actual_dep DATETIME NULL COMMENT '飞常准：替代航班实际起飞',
    avi_alt_actual_arr DATETIME NULL COMMENT '飞常准：替代航班实际到达',

    -- 航班场景
    flight_scenario VARCHAR(32) NULL COMMENT 'direct/connecting/rebooking/multi_rebooking/cancelled_nofly',
    rebooking_count TINYINT NOT NULL DEFAULT 0 COMMENT '改签次数',

    -- 联程汇总
    is_connecting TINYINT(1) NULL COMMENT '是否联程',
    total_segments TINYINT NULL COMMENT '联程段数',
    origin_iata VARCHAR(8) NULL COMMENT '全程出发IATA',
    destination_iata VARCHAR(8) NULL COMMENT '全程目的地IATA',
    missed_connection TINYINT(1) NULL COMMENT '是否接驳失误',

    -- 延误计算追溯
    delay_duration_minutes INT NULL COMMENT '延误时长（分钟）',
    delay_reason VARCHAR(128) NULL COMMENT '延误原因',
    delay_type VARCHAR(32) NULL COMMENT '延误类型',
    delay_calc_from VARCHAR(64) NULL COMMENT '延误起算时间点来源字段名',
    delay_calc_to VARCHAR(64) NULL COMMENT '延误终止时间点来源字段名',

    UNIQUE KEY uk_forceid (forceid),
    KEY idx_flight_no (flight_no),
    KEY idx_flight_scenario (flight_scenario),
    KEY idx_avi_status (avi_status),
    KEY idx_alt_flight_no (alt_flight_no),
    KEY idx_is_connecting (is_connecting),
    KEY idx_origin_dest (origin_iata, destination_iata),
    KEY idx_missed_connection (missed_connection)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='航班延误专属数据表';

-- ============================================
-- Step 2: 新建 ai_baggage_delay_data 表
-- ============================================
CREATE TABLE IF NOT EXISTS ai_baggage_delay_data (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '关联主表（唯一键）',

    -- 延误时长计算
    first_flight_actual_arr_time DATETIME NULL COMMENT '延误起算点：首次乘坐航班实际到达时间',
    baggage_receipt_time DATETIME NULL COMMENT '延误终止点：行李实际签收时间',
    baggage_delay_hours DECIMAL(5,1) NULL COMMENT '延误小时数，自动计算',
    baggage_delay_calc_basis VARCHAR(32) NULL COMMENT '计算依据：receipt/transfer_flight/estimated',

    -- 赔付门槛与金额
    delay_tier VARCHAR(16) NULL COMMENT '延误档位：6-12h/12-18h/18h+',
    payout_tier_amount DECIMAL(10,2) NULL COMMENT '档位对应金额：500/1000/1500',
    claim_amount DECIMAL(10,2) NULL COMMENT '申请人索赔金额',
    final_payout_amount DECIMAL(10,2) NULL COMMENT '最终赔付（取min(档位金额, 索赔金额, 保额上限)）',
    payout_calibration_reason VARCHAR(256) NULL COMMENT '金额校准原因',

    -- 证明材料
    has_baggage_receipt_proof CHAR(1) NULL COMMENT '是否有签收证明（Y/N）',
    has_baggage_delay_proof CHAR(1) NULL COMMENT '是否有延误证明（Y/N）',
    has_baggage_tag CHAR(1) NULL COMMENT '是否有行李牌（Y/N）',
    pir_no VARCHAR(64) NULL COMMENT 'PIR不正常行李报告编号',
    has_pir_report CHAR(1) NULL COMMENT '是否有PIR报告（Y/N）',

    UNIQUE KEY uk_forceid (forceid),
    KEY idx_delay_tier (delay_tier),
    KEY idx_first_arr_time (first_flight_actual_arr_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='行李延误专属数据表';

-- ============================================
-- Step 3: ai_review_result 新增缺失列 + AI追溯列
-- ============================================

-- 缺失列（代码已引用但表中不存在）
-- 注：MySQL 不支持 ADD COLUMN IF NOT EXISTS，重复列会报 1060 错误，由执行脚本忽略
ALTER TABLE ai_review_result ADD COLUMN claim_type VARCHAR(32) NULL COMMENT '案件类型（flight_delay/baggage_delay）' AFTER claim_id;
ALTER TABLE ai_review_result ADD COLUMN benefit_name VARCHAR(64) NULL COMMENT '险种名称' AFTER claim_type;
ALTER TABLE ai_review_result ADD COLUMN insured_name VARCHAR(128) NULL COMMENT '被保险人姓名' AFTER applicant_name;
ALTER TABLE ai_review_result ADD COLUMN final_decision VARCHAR(32) NULL COMMENT '最终决定（approve/reject/supplement）' AFTER auditor;
ALTER TABLE ai_review_result ADD COLUMN manual_status VARCHAR(32) NULL COMMENT '人工处理状态' AFTER forwarded_at;
ALTER TABLE ai_review_result ADD COLUMN manual_conclusion TEXT NULL COMMENT '人工审核结论' AFTER manual_status;

-- AI追溯列
ALTER TABLE ai_review_result ADD COLUMN ai_model_version VARCHAR(32) NULL COMMENT 'AI模型版本标识' AFTER manual_conclusion;
ALTER TABLE ai_review_result ADD COLUMN pipeline_version VARCHAR(32) NULL COMMENT '审核pipeline版本' AFTER ai_model_version;
ALTER TABLE ai_review_result ADD COLUMN rule_ids_hit TEXT NULL COMMENT '命中的规则ID列表（JSON数组）' AFTER pipeline_version;
ALTER TABLE ai_review_result ADD COLUMN audit_reason_tags TEXT NULL COMMENT '审核原因结构化标签（JSON数组）' AFTER rule_ids_hit;
ALTER TABLE ai_review_result ADD COLUMN human_override VARCHAR(32) NULL COMMENT '人工是否覆盖AI结论' AFTER audit_reason_tags;

-- 新增索引（如已存在会报 Duplicate key name，脚本会忽略此错误）
ALTER TABLE ai_review_result ADD INDEX idx_claim_type (claim_type);
ALTER TABLE ai_review_result ADD INDEX idx_benefit_name (benefit_name);

SELECT 'Stage 1 migration completed: sub-tables created + main table columns added' AS status;
