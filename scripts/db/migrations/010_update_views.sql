-- 数据库结构改进 - 第4阶段：更新视图
-- 版本: 010
-- 说明: 更新视图以 LEFT JOIN 子表，确保应用层查询不受影响

-- ============================================
-- Step 7a: 更新 v_claim_audit_summary 视图
-- ============================================
CREATE OR REPLACE VIEW v_claim_audit_summary AS
SELECT
    r.forceid AS '案件ID',
    r.claim_id AS '案件编号',
    r.claim_type AS '案件类型',
    r.benefit_name AS '险种名称',
    r.applicant_name AS '申请人',
    r.insured_name AS '被保险人',
    r.passenger_id_number AS '证件号',
    r.policy_no AS '保单号',
    r.insurer AS '保险公司',

    -- 航班延误专属字段（LEFT JOIN）
    f.flight_no AS '航班号',
    f.dep_city AS '出发城市',
    f.arr_city AS '目的城市',
    f.planned_dep_time AS '计划起飞',
    f.actual_dep_time AS '实际起飞',
    f.delay_duration_minutes AS '延误(分钟)',
    f.delay_reason AS '延误原因',

    -- 行李延误专属字段（LEFT JOIN）
    b.baggage_receipt_time AS '行李签收时间',
    b.first_flight_actual_arr_time AS '行李延误起算点',
    b.baggage_delay_hours AS '行李延误(小时)',
    b.delay_tier AS '行李延误档位',

    -- 审核结论（主表）
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额',
    r.payout_currency AS '币种',
    r.is_additional AS '需补件',
    r.supplementary_reason AS '补件原因',
    r.audit_time AS '审核时间',
    r.auditor AS '审核员',

    -- 状态信息
    s.current_status AS '流程状态',
    s.download_status AS '下载状态',
    s.review_status AS '审核状态',
    r.created_at AS '创建时间'
FROM ai_review_result r
LEFT JOIN ai_claim_status s ON r.forceid = s.forceid
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
LEFT JOIN ai_baggage_delay_data b ON r.forceid = b.forceid;

-- ============================================
-- Step 7b: 更新/创建 v_audit_statistics 视图
-- ============================================
CREATE OR REPLACE VIEW v_audit_statistics AS
SELECT
    DATE(r.audit_time) AS audit_date,
    r.claim_type,
    r.benefit_name,
    COUNT(*) AS total_count,
    SUM(r.audit_result = '通过') AS pass_count,
    SUM(r.audit_result = '拒绝') AS reject_count,
    SUM(r.is_additional = 'Y') AS supplementary_count,
    AVG(f.delay_duration_minutes) AS avg_delay_minutes,
    SUM(r.payout_amount) AS total_payout
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.audit_time IS NOT NULL
GROUP BY DATE(r.audit_time), r.claim_type, r.benefit_name;

SELECT 'Stage 4 migration completed: views updated' AS status;
