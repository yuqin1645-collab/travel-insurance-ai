-- ============================================
-- ai_review_result 表 - 全字段查询
-- ============================================

-- 1. 查询所有记录的所有字段
SELECT * FROM ai_review_result ORDER BY created_at DESC;

-- 2. 查询指定案件ID的所有字段
SELECT * FROM ai_review_result WHERE forceid = 'YOUR_FORCEID';

-- 3. 全字段带中文别名查询
SELECT
    -- 基础信息
    id AS '主键ID',
    forceid AS '案件ID',
    claim_id AS '案件编号',

    -- 申请人信息
    applicant_name AS '申请人姓名',
    insured_name AS '被保险人姓名',
    passenger_id_type AS '证件类型',
    passenger_id_number AS '证件号码',

    -- 保单信息
    policy_no AS '保单号',
    insurer AS '保险公司',
    policy_effective_date AS '保单生效日期',
    policy_expiry_date AS '保单截止日期',
    insured_amount AS '保额',
    remaining_coverage AS '剩余保额',

    -- 航班信息
    flight_no AS '航班号',
    operating_carrier AS '承运人',
    dep_iata AS '出发机场IATA',
    arr_iata AS '到达机场IATA',
    dep_city AS '出发城市',
    arr_city AS '目的城市',
    dep_country AS '出发国家',
    arr_country AS '目的国家',

    -- 航班时间
    planned_dep_time AS '计划起飞时间',
    actual_dep_time AS '实际起飞时间',
    planned_arr_time AS '计划到达时间',
    actual_arr_time AS '实际到达时间',
    alt_dep_time AS '替代航班起飞时间',
    alt_arr_time AS '替代航班到达时间',

    -- 延误信息
    delay_duration_minutes AS '延误时长(分钟)',
    delay_reason AS '延误原因',
    delay_type AS '延误类型',

    -- 审核结果
    audit_result AS '审核结果',
    audit_status AS '审核状态',
    confidence_score AS '置信度(%)',
    audit_time AS '审核时间',
    auditor AS '审核员',

    -- 赔付信息
    payout_amount AS '赔付金额',
    payout_currency AS '赔付币种',
    payout_basis AS '赔付依据',

    -- 补件信息
    is_additional AS '需补件',
    supplementary_count AS '补件次数',
    supplementary_reason AS '补件原因',
    supplementary_deadline AS '补件截止时间',

    -- 审核结论
    remark AS '审核备注',
    key_conclusions AS '关键结论',
    decision_reason AS '核赔意见',

    -- 逻辑校验
    identity_match AS '身份匹配',
    threshold_met AS '达到赔付门槛',
    exclusion_triggered AS '触发免责',
    exclusion_reason AS '免责原因',

    -- 推送状态
    forwarded_to_frontend AS '已推送到前端',
    forwarded_at AS '推送时间',
    frontend_response AS '前端响应',

    -- 其他
    raw_result AS '原始审核结果JSON',
    metadata AS '元数据',
    created_at AS '创建时间',
    updated_at AS '更新时间'
FROM ai_review_result
ORDER BY created_at DESC;

-- 4. 按条件筛选查询
SELECT * FROM ai_review_result
WHERE audit_result = 'approved'  -- 可替换条件
ORDER BY created_at DESC;

-- 5. 分页查询
SELECT * FROM ai_review_result
ORDER BY created_at DESC
LIMIT 0, 50;  -- 跳过0条，取50条