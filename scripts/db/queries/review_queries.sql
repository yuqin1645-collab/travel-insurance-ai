-- ============================================
-- 航班延误AI审核系统 - 常用查询语句
-- ============================================

-- ============================================
-- 一、审核结果查询
-- ============================================

-- 1. 查询所有审核结果
SELECT
    forceid AS '案件ID',
    claim_id AS '案件编号',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    dep_iata AS '出发',
    arr_iata AS '到达',
    delay_duration_minutes AS '延误时长(分)',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额',
    is_additional AS '需补件',
    created_at AS '创建时间'
FROM ai_review_result
ORDER BY created_at DESC
LIMIT 100;

-- 2. 按审核结果统计
SELECT
    audit_result AS '审核结果',
    COUNT(*) AS '数量',
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM ai_review_result), 2) AS '占比(%)'
FROM ai_review_result
GROUP BY audit_result;

-- 3. 查询通过的案件
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    delay_duration_minutes AS '延误时长(分)',
    payout_amount AS '赔付金额',
    confidence_score AS '置信度(%)',
    audit_time AS '审核时间'
FROM ai_review_result
WHERE audit_result = 'approved'
ORDER BY audit_time DESC;

-- 4. 查询拒绝的案件
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

-- 5. 查询需要补件的案件
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    supplementary_count AS '补件次数',
    supplementary_reason AS '补件原因',
    supplementary_deadline AS '补件截止时间',
    created_at AS '创建时间'
FROM ai_review_result
WHERE is_additional = 'Y'
ORDER BY created_at DESC;

-- ============================================
-- 二、航班信息查询
-- ============================================

-- 6. 按航班号查询
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    flight_no AS '航班号',
    operating_carrier AS '承运人',
    dep_iata AS '出发',
    arr_iata AS '到达',
    dep_city AS '出发城市',
    arr_city AS '目的城市',
    delay_duration_minutes AS '延误时长(分)',
    audit_result AS '审核结果'
FROM ai_review_result
WHERE flight_no LIKE '%MU%'  -- 替换为要查询的航班号
ORDER BY created_at DESC;

-- 7. 按航线查询（出发地-目的地）
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

-- 8. 延误时长分布统计
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
-- 三、保单信息查询
-- ============================================

-- 9. 按保单号查询
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
WHERE policy_no = 'YOUR_POLICY_NO'  -- 替换为实际保单号
ORDER BY created_at DESC;

-- 10. 按保险公司统计
SELECT
    insurer AS '保险公司',
    COUNT(*) AS '案件数',
    SUM(CASE WHEN audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过数',
    SUM(CASE WHEN audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝数',
    SUM(payout_amount) AS '总赔付金额',
    ROUND(AVG(payout_amount), 2) AS '平均赔付'
FROM ai_review_result
WHERE insurer IS NOT NULL
GROUP BY insurer
ORDER BY COUNT(*) DESC;

-- ============================================
-- 四、被保险人信息查询
-- ============================================

-- 11. 按被保险人姓名查询
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    passenger_id_type AS '证件类型',
    passenger_id_number AS '证件号码',
    flight_no AS '航班号',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额'
FROM ai_review_result
WHERE passenger_name LIKE '%张%'  -- 替换为要查询的姓名
ORDER BY created_at DESC;

-- 12. 按证件号码查询
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
-- 五、赔付信息查询
-- ============================================

-- 13. 赔付金额统计
SELECT
    COUNT(*) AS '总案件数',
    SUM(CASE WHEN payout_amount > 0 THEN 1 ELSE 0 END) AS '赔付案件数',
    SUM(payout_amount) AS '总赔付金额',
    ROUND(AVG(payout_amount), 2) AS '平均赔付',
    MAX(payout_amount) AS '最高赔付',
    MIN(payout_amount) AS '最低赔付'
FROM ai_review_result
WHERE audit_result = 'approved';

-- 14. 按赔付金额区间统计
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
        WHEN payout_amount <= 1000 THEN '1000-2000'
        ELSE '2000以上'
    END
ORDER BY MIN(payout_amount);

-- ============================================
-- 六、逻辑校验查询
-- ============================================

-- 15. 身份匹配校验结果
SELECT
    identity_match AS '身份匹配',
    COUNT(*) AS '数量'
FROM ai_review_result
WHERE identity_match IS NOT NULL
GROUP BY identity_match;

-- 16. 赔付门槛校验结果
SELECT
    threshold_met AS '达到门槛',
    COUNT(*) AS '数量'
FROM ai_review_result
WHERE threshold_met IS NOT NULL
GROUP BY threshold_met;

-- 17. 免责情形校验结果
SELECT
    exclusion_triggered AS '触发免责',
    COUNT(*) AS '数量',
    GROUP_CONCAT(DISTINCT exclusion_reason SEPARATOR '; ') AS '免责原因'
FROM ai_review_result
WHERE exclusion_triggered = 'Y'
GROUP BY exclusion_triggered;

-- ============================================
-- 七、时间维度查询
-- ============================================

-- 18. 按日期统计审核数量
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

-- 19. 按月份统计
SELECT
    DATE_FORMAT(created_at, '%Y-%m') AS '月份',
    COUNT(*) AS '审核数量',
    SUM(payout_amount) AS '赔付金额',
    ROUND(AVG(delay_duration_minutes), 0) AS '平均延误(分)'
FROM ai_review_result
GROUP BY DATE_FORMAT(created_at, '%Y-%m')
ORDER BY DATE_FORMAT(created_at, '%Y-%m') DESC;

-- 20. 按小时统计（查看审核高峰时段）
SELECT
    HOUR(created_at) AS '小时',
    COUNT(*) AS '审核数量'
FROM ai_review_result
GROUP BY HOUR(created_at)
ORDER BY HOUR(created_at);

-- ============================================
-- 八、前端推送状态查询
-- ============================================

-- 21. 推送状态统计
SELECT
    forwarded_to_frontend AS '已推送',
    COUNT(*) AS '数量'
FROM ai_review_result
GROUP BY forwarded_to_frontend;

-- 22. 未推送的案件
SELECT
    forceid AS '案件ID',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额',
    audit_time AS '审核时间'
FROM ai_review_result
WHERE forwarded_to_frontend = 0 AND audit_result IS NOT NULL
ORDER BY audit_time DESC;

-- 23. 推送失败的案件
SELECT
    forceid AS '案件ID',
    frontend_response AS '前端响应',
    audit_time AS '审核时间'
FROM ai_review_result
WHERE forwarded_to_frontend = 1 AND frontend_response LIKE '%error%'
ORDER BY forwarded_at DESC;

-- ============================================
-- 九、综合报表查询
-- ============================================

-- 24. 审核汇总报表
SELECT
    DATE(created_at) AS '日期',
    COUNT(*) AS '总案件数',
    SUM(CASE WHEN audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过',
    SUM(CASE WHEN audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝',
    SUM(CASE WHEN is_additional = 'Y' THEN 1 ELSE 0 END) AS '需补件',
    ROUND(AVG(delay_duration_minutes), 0) AS '平均延误(分)',
    SUM(payout_amount) AS '总赔付'
FROM ai_review_result
WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
GROUP BY DATE(created_at)
ORDER BY DATE(created_at) DESC;

-- 25. 案件详情查询（全部字段）
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
    delay_type AS '延误类型',
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
    supplementary_deadline AS '补件截止',
    remark AS '审核备注',
    decision_reason AS '核赔意见',
    identity_match AS '身份匹配',
    threshold_met AS '达到门槛',
    exclusion_triggered AS '触发免责',
    exclusion_reason AS '免责原因',
    forwarded_to_frontend AS '已推送',
    forwarded_at AS '推送时间',
    created_at AS '创建时间',
    updated_at AS '更新时间'
FROM ai_review_result
WHERE forceid = 'YOUR_FORCEID'  -- 替换为实际案件ID
LIMIT 1;

-- ============================================
-- 十、关联查询
-- ============================================

-- 26. 关联案件状态表
SELECT
    r.forceid AS '案件ID',
    r.passenger_name AS '被保险人',
    r.flight_no AS '航班号',
    r.audit_result AS '审核结果',
    s.current_status AS '流程状态',
    s.download_status AS '下载状态',
    s.review_status AS '审核状态',
    s.supplementary_count AS '补件次数',
    s.error_message AS '错误信息'
FROM ai_review_result r
LEFT JOIN ai_claim_status s ON r.forceid = s.forceid
ORDER BY r.created_at DESC
LIMIT 50;

-- 27. 关联补件记录表
SELECT
    r.forceid AS '案件ID',
    r.passenger_name AS '被保险人',
    r.is_additional AS '需补件',
    sr.supplementary_number AS '补件次数',
    sr.requested_reason AS '补件原因',
    sr.deadline AS '截止时间',
    sr.status AS '补件状态'
FROM ai_review_result r
LEFT JOIN ai_supplementary_records sr ON r.forceid = sr.forceid
WHERE r.is_additional = 'Y'
ORDER BY sr.deadline DESC;

-- ============================================
-- 十一、数据质量检查
-- ============================================

-- 28. 字段填充率检查
SELECT
    '总记录数' AS '指标',
    COUNT(*) AS '值'
FROM ai_review_result
UNION ALL
SELECT
    '有审核结果',
    SUM(CASE WHEN audit_result IS NOT NULL AND audit_result != '' THEN 1 ELSE 0 END)
FROM ai_review_result
UNION ALL
SELECT
    '有被保险人姓名',
    SUM(CASE WHEN passenger_name IS NOT NULL AND passenger_name != '' THEN 1 ELSE 0 END)
FROM ai_review_result
UNION ALL
SELECT
    '有航班号',
    SUM(CASE WHEN flight_no IS NOT NULL AND flight_no != '' THEN 1 ELSE 0 END)
FROM ai_review_result
UNION ALL
SELECT
    '有延误时长',
    SUM(CASE WHEN delay_duration_minutes IS NOT NULL THEN 1 ELSE 0 END)
FROM ai_review_result
UNION ALL
SELECT
    '有赔付金额',
    SUM(CASE WHEN payout_amount IS NOT NULL THEN 1 ELSE 0 END)
FROM ai_review_result;

-- 29. 检查重复记录
SELECT
    forceid,
    COUNT(*) AS '重复次数'
FROM ai_review_result
GROUP BY forceid
HAVING COUNT(*) > 1;

-- 30. 检查异常数据（通过但无赔付金额）
SELECT
    forceid AS '案件ID',
    passenger_name AS '被保险人',
    audit_result AS '审核结果',
    payout_amount AS '赔付金额'
FROM ai_review_result
WHERE audit_result = 'approved' AND (payout_amount IS NULL OR payout_amount = 0);