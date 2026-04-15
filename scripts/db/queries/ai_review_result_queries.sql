-- ============================================
-- ai_review_result 表 - 常用查询语句
-- ============================================

-- ============================================
-- 一、基础查询
-- ============================================

-- 1. 查看所有字段
SELECT * FROM ai_review_result LIMIT 10;

-- 2. 总记录数
SELECT COUNT(*) AS '总记录数' FROM ai_review_result;

-- 3. 字段填充率统计
SELECT
    '总记录数' AS '指标',
    COUNT(*) AS '数量'
FROM ai_review_result
UNION ALL
SELECT '有审核结果', SUM(CASE WHEN audit_result IS NOT NULL AND audit_result != '' THEN 1 ELSE 0 END) FROM ai_review_result
UNION ALL
SELECT '有被保险人姓名', SUM(CASE WHEN passenger_name IS NOT NULL AND passenger_name != '' THEN 1 ELSE 0 END) FROM ai_review_result
UNION ALL
SELECT '有证件号', SUM(CASE WHEN passenger_id_number IS NOT NULL AND passenger_id_number != '' THEN 1 ELSE 0 END) FROM ai_review_result
UNION ALL
SELECT '有航班号', SUM(CASE WHEN flight_no IS NOT NULL AND flight_no != '' THEN 1 ELSE 0 END) FROM ai_review_result
UNION ALL
SELECT '有延误时长', SUM(CASE WHEN delay_duration_minutes IS NOT NULL THEN 1 ELSE 0 END) FROM ai_review_result
UNION ALL
SELECT '有赔付金额', SUM(CASE WHEN payout_amount IS NOT NULL THEN 1 ELSE 0 END) FROM ai_review_result;

-- ============================================
-- 二、审核结果查询
-- ============================================

-- 4. 按审核结果统计
SELECT
    COALESCE(audit_result, '(空)') AS '审核结果',
    COUNT(*) AS '数量',
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM ai_review_result), 2) AS '占比(%)'
FROM ai_review_result
GROUP BY audit_result;

-- 5. 查询通过的案件
SELECT
    forceid AS '案件ID',
    claim_id AS '案件编号',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    dep_iata AS '出发',
    arr_iata AS '到达',
    delay_duration_minutes AS '延误时长(分)',
    payout_amount AS '赔付金额',
    confidence_score AS '置信度(%)',
    audit_time AS '审核时间'
FROM ai_review_result
WHERE audit_result = 'approved'
ORDER BY audit_time DESC;

-- 6. 查询拒绝的案件
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    remark AS '审核备注',
    decision_reason AS '核赔意见',
    audit_time AS '审核时间'
FROM ai_review_result
WHERE audit_result = 'rejected'
ORDER BY audit_time DESC;

-- 7. 查询需补件的案件
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    supplementary_count AS '补件次数',
    supplementary_reason AS '补件原因',
    is_additional AS '需补件'
FROM ai_review_result
WHERE is_additional = 'Y' OR audit_result = 'supplementary_needed'
ORDER BY created_at DESC;

-- ============================================
-- 三、航班信息查询
-- ============================================

-- 8. 按航班号查询
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    operating_carrier AS '承运人',
    dep_iata AS '出发',
    arr_iata AS '到达',
    delay_duration_minutes AS '延误时长(分)',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额'
FROM ai_review_result
WHERE flight_no LIKE '%MU%'  -- 替换为要查询的航班号
ORDER BY created_at DESC;

-- 9. 按航线查询
SELECT
    forceid AS '案件ID',
    flight_no AS '航班号',
    dep_iata AS '出发',
    arr_iata AS '到达',
    dep_city AS '出发城市',
    arr_city AS '目的城市',
    delay_duration_minutes AS '延误时长(分)',
    audit_result AS '审核结果'
FROM ai_review_result
WHERE dep_iata = 'PVG' AND arr_iata = 'LAX'  -- 替换为实际IATA代码
ORDER BY created_at DESC;

-- 10. 延误时长分布
SELECT
    CASE
        WHEN delay_duration_minutes < 120 THEN '0-2小时'
        WHEN delay_duration_minutes < 240 THEN '2-4小时'
        WHEN delay_duration_minutes < 480 THEN '4-8小时'
        ELSE '8小时以上'
    END AS '延误时长区间',
    COUNT(*) AS '数量',
    ROUND(AVG(delay_duration_minutes), 0) AS '平均延误(分)'
FROM ai_review_result
WHERE delay_duration_minutes IS NOT NULL
GROUP BY
    CASE
        WHEN delay_duration_minutes < 120 THEN '0-2小时'
        WHEN delay_duration_minutes < 240 THEN '2-4小时'
        WHEN delay_duration_minutes < 480 THEN '4-8小时'
        ELSE '8小时以上'
    END
ORDER BY MIN(delay_duration_minutes);

-- ============================================
-- 四、保单信息查询
-- ============================================

-- 11. 按保单号查询
SELECT
    forceid AS '案件ID',
    policy_no AS '保单号',
    insurer AS '保险公司',
    passenger_name AS '被保险人',
    policy_effective_date AS '生效日期',
    policy_expiry_date AS '截止日期',
    insured_amount AS '保额',
    payout_amount AS '赔付金额'
FROM ai_review_result
WHERE policy_no IS NOT NULL AND policy_no != ''
ORDER BY created_at DESC;

-- 12. 按保险公司统计
SELECT
    COALESCE(insurer, '(空)') AS '保险公司',
    COUNT(*) AS '案件数',
    SUM(CASE WHEN audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过数',
    SUM(CASE WHEN audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝数',
    SUM(payout_amount) AS '总赔付金额',
    ROUND(AVG(payout_amount), 2) AS '平均赔付'
FROM ai_review_result
GROUP BY insurer
ORDER BY COUNT(*) DESC;

-- ============================================
-- 五、被保险人信息查询
-- ============================================

-- 13. 按被保险人姓名查询
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    passenger_id_type AS '证件类型',
    passenger_id_number AS '证件号码',
    flight_no AS '航班号',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额'
FROM ai_review_result
WHERE passenger_name IS NOT NULL AND passenger_name != ''
ORDER BY created_at DESC;

-- 14. 按证件号码查询
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    passenger_id_number AS '证件号码',
    flight_no AS '航班号',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额'
FROM ai_review_result
WHERE passenger_id_number = 'YOUR_ID_NUMBER'  -- 替换为实际证件号
ORDER BY created_at DESC;

-- ============================================
-- 六、赔付信息查询
-- ============================================

-- 15. 赔付金额统计
SELECT
    COUNT(*) AS '总案件数',
    SUM(CASE WHEN payout_amount > 0 THEN 1 ELSE 0 END) AS '赔付案件数',
    SUM(payout_amount) AS '总赔付金额',
    ROUND(AVG(payout_amount), 2) AS '平均赔付',
    MAX(payout_amount) AS '最高赔付',
    MIN(payout_amount) AS '最低赔付'
FROM ai_review_result
WHERE audit_result = 'approved';

-- 16. 按赔付金额区间统计
SELECT
    CASE
        WHEN payout_amount <= 500 THEN '0-500'
        WHEN payout_amount <= 1000 THEN '500-1000'
        WHEN payout_amount <= 2000 THEN '1000-2000'
        ELSE '2000以上'
    END AS '赔付区间',
    COUNT(*) AS '数量'
FROM ai_review_result
WHERE payout_amount IS NOT NULL AND audit_result = 'approved'
GROUP BY
    CASE
        WHEN payout_amount <= 500 THEN '0-500'
        WHEN payout_amount <= 1000 THEN '500-1000'
        WHEN payout_amount <= 2000 THEN '1000-2000'
        ELSE '2000以上'
    END
ORDER BY MIN(payout_amount);

-- ============================================
-- 七、时间维度查询
-- ============================================

-- 17. 按日期统计审核数量
SELECT
    DATE(created_at) AS '日期',
    COUNT(*) AS '审核数量',
    SUM(CASE WHEN audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过数',
    SUM(CASE WHEN audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝数',
    SUM(payout_amount) AS '赔付金额'
FROM ai_review_result
GROUP BY DATE(created_at)
ORDER BY DATE(created_at) DESC
LIMIT 30;

-- 18. 按月份统计
SELECT
    DATE_FORMAT(created_at, '%Y-%m') AS '月份',
    COUNT(*) AS '审核数量',
    SUM(payout_amount) AS '赔付金额',
    ROUND(AVG(delay_duration_minutes), 0) AS '平均延误(分)'
FROM ai_review_result
GROUP BY DATE_FORMAT(created_at, '%Y-%m')
ORDER BY DATE_FORMAT(created_at, '%Y-%m') DESC;

-- ============================================
-- 八、完整字段查询
-- ============================================

-- 19. 查询全部字段（单条详情）
SELECT
    forceid AS '案件ID',
    claim_id AS '案件编号',
    passenger_name AS '被保险人',
    passenger_id_type AS '证件类型',
    passenger_id_number AS '证件号码',
    policy_no AS '保单号',
    insurer AS '保险公司',
    policy_effective_date AS '保单生效',
    policy_expiry_date AS '保单截止',
    flight_no AS '航班号',
    operating_carrier AS '承运人',
    dep_iata AS '出发IATA',
    arr_iata AS '到达IATA',
    dep_city AS '出发城市',
    arr_city AS '目的城市',
    planned_dep_time AS '计划起飞',
    actual_dep_time AS '实际起飞',
    planned_arr_time AS '计划到达',
    actual_arr_time AS '实际到达',
    delay_duration_minutes AS '延误时长(分)',
    delay_reason AS '延误原因',
    audit_result AS '审核结果',
    audit_status AS '审核状态',
    confidence_score AS '置信度(%)',
    audit_time AS '审核时间',
    auditor AS '审核员',
    payout_amount AS '赔付金额',
    payout_currency AS '币种',
    payout_basis AS '赔付依据',
    insured_amount AS '保额',
    remaining_coverage AS '剩余保额',
    is_additional AS '需补件',
    supplementary_count AS '补件次数',
    supplementary_reason AS '补件原因',
    remark AS '审核备注',
    decision_reason AS '核赔意见',
    identity_match AS '身份匹配',
    threshold_met AS '达到门槛',
    exclusion_triggered AS '触发免责',
    exclusion_reason AS '免责原因',
    forwarded_to_frontend AS '已推送',
    created_at AS '创建时间',
    updated_at AS '更新时间'
FROM ai_review_result
ORDER BY created_at DESC;

-- 20. 按案件ID查询详情
SELECT * FROM ai_review_result WHERE forceid = 'YOUR_FORCEID';