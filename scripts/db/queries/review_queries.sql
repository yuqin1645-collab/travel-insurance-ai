-- ============================================
-- 航班延误AI审核系统 - 常用查询语句
-- 已适配拆分后的表结构（主表 + 子表 LEFT JOIN）
-- ============================================

-- ============================================
-- 一、审核结果查询
-- ============================================

-- 1. 查询所有审核结果（含航班/行李子表字段）
SELECT
    r.forceid AS '案件ID',
    r.claim_id AS '案件编号',
    r.claim_type AS '案件类型',
    r.benefit_name AS '险种名称',
    r.applicant_name AS '申请人',
    r.insured_name AS '被保险人',
    f.flight_no AS '航班号',
    f.dep_iata AS '出发',
    f.arr_iata AS '到达',
    f.dep_city AS '出发城市',
    f.arr_city AS '目的城市',
    f.delay_duration_minutes AS '延误时长(分)',
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额',
    r.is_additional AS '需补件',
    r.created_at AS '创建时间'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
LEFT JOIN ai_baggage_delay_data b ON r.forceid = b.forceid
ORDER BY r.created_at DESC
LIMIT 100;

-- 2. 按审核结果统计
SELECT
    r.audit_result AS '审核结果',
    COUNT(*) AS '数量',
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM ai_review_result), 2) AS '占比(%)'
FROM ai_review_result r
GROUP BY r.audit_result;

-- 3. 查询通过的案件
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    f.flight_no AS '航班号',
    f.delay_duration_minutes AS '延误时长(分)',
    r.payout_amount AS '赔付金额',
    r.confidence_score AS '置信度(%)',
    r.audit_time AS '审核时间'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.audit_result = 'approved'
ORDER BY r.audit_time DESC;

-- 4. 查询拒绝的案件
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    f.flight_no AS '航班号',
    r.remark AS '审核备注',
    r.decision_reason AS '核赔意见',
    r.audit_time AS '审核时间'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.audit_result = 'rejected'
ORDER BY r.audit_time DESC;

-- 5. 查询需要补件的案件
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    f.flight_no AS '航班号',
    r.supplementary_reason AS '补件原因',
    r.created_at AS '创建时间'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.is_additional = 'Y'
ORDER BY r.created_at DESC;

-- ============================================
-- 二、航班信息查询（仅航班延误案件）
-- ============================================

-- 6. 按航班号查询
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    f.flight_no AS '航班号',
    f.operating_carrier AS '承运人',
    f.dep_iata AS '出发',
    f.arr_iata AS '到达',
    f.dep_city AS '出发城市',
    f.arr_city AS '目的城市',
    f.delay_duration_minutes AS '延误时长(分)',
    r.audit_result AS '审核结果'
FROM ai_review_result r
JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE f.flight_no LIKE '%MU%'
ORDER BY r.created_at DESC;

-- 7. 按航线查询（出发地-目的地）
SELECT
    r.forceid AS '案件ID',
    f.flight_no AS '航班号',
    f.dep_iata AS '出发',
    f.arr_iata AS '到达',
    f.dep_city AS '出发城市',
    f.arr_city AS '目的城市',
    f.delay_duration_minutes AS '延误时长(分)',
    r.audit_result AS '审核结果'
FROM ai_review_result r
JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE f.dep_iata = 'PVG' AND f.arr_iata = 'LAX'
ORDER BY r.created_at DESC;

-- 8. 延误时长分布统计
SELECT
    CASE
        WHEN f.delay_duration_minutes < 120 THEN '0-2小时'
        WHEN f.delay_duration_minutes < 240 THEN '2-4小时'
        WHEN f.delay_duration_minutes < 480 THEN '4-8小时'
        ELSE '8小时以上'
    END AS '延误时长区间',
    COUNT(*) AS '数量',
    ROUND(AVG(f.delay_duration_minutes), 0) AS '平均延误(分)'
FROM ai_review_result r
JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE f.delay_duration_minutes IS NOT NULL
GROUP BY
    CASE
        WHEN f.delay_duration_minutes < 120 THEN '0-2小时'
        WHEN f.delay_duration_minutes < 240 THEN '2-4小时'
        WHEN f.delay_duration_minutes < 480 THEN '4-8小时'
        ELSE '8小时以上'
    END
ORDER BY MIN(f.delay_duration_minutes);

-- ============================================
-- 三、保单信息查询
-- ============================================

-- 9. 按保单号查询
SELECT
    r.forceid AS '案件ID',
    r.policy_no AS '保单号',
    r.insurer AS '保险公司',
    r.applicant_name AS '申请人',
    r.policy_effective_date AS '生效日期',
    r.policy_expiry_date AS '截止日期',
    r.insured_amount AS '保额',
    r.payout_amount AS '赔付金额'
FROM ai_review_result r
WHERE r.policy_no = 'YOUR_POLICY_NO'
ORDER BY r.created_at DESC;

-- 10. 按保险公司统计
SELECT
    r.insurer AS '保险公司',
    COUNT(*) AS '案件数',
    SUM(CASE WHEN r.audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过数',
    SUM(CASE WHEN r.audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝数',
    SUM(r.payout_amount) AS '总赔付金额',
    ROUND(AVG(r.payout_amount), 2) AS '平均赔付'
FROM ai_review_result r
WHERE r.insurer IS NOT NULL
GROUP BY r.insurer
ORDER BY COUNT(*) DESC;

-- ============================================
-- 四、申请人信息查询
-- ============================================

-- 11. 按被保险人姓名查询
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    r.passenger_id_type AS '证件类型',
    r.passenger_id_number AS '证件号码',
    f.flight_no AS '航班号',
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.applicant_name LIKE '%张%'
ORDER BY r.created_at DESC;

-- 12. 按证件号码查询
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    r.passenger_id_number AS '证件号码',
    f.flight_no AS '航班号',
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.passenger_id_number = 'YOUR_ID_NUMBER'
ORDER BY r.created_at DESC;

-- ============================================
-- 五、赔付信息查询
-- ============================================

-- 13. 赔付金额统计
SELECT
    COUNT(*) AS '总案件数',
    SUM(CASE WHEN r.payout_amount > 0 THEN 1 ELSE 0 END) AS '赔付案件数',
    SUM(r.payout_amount) AS '总赔付金额',
    ROUND(AVG(r.payout_amount), 2) AS '平均赔付',
    MAX(r.payout_amount) AS '最高赔付',
    MIN(r.payout_amount) AS '最低赔付'
FROM ai_review_result r
WHERE r.audit_result = 'approved';

-- 14. 按赔付金额区间统计
SELECT
    CASE
        WHEN r.payout_amount <= 500 THEN '0-500'
        WHEN r.payout_amount <= 1000 THEN '500-1000'
        WHEN r.payout_amount <= 2000 THEN '1000-2000'
        ELSE '2000以上'
    END AS '赔付区间',
    COUNT(*) AS '数量'
FROM ai_review_result r
WHERE r.payout_amount IS NOT NULL AND r.audit_result = 'approved'
GROUP BY
    CASE
        WHEN r.payout_amount <= 500 THEN '0-500'
        WHEN r.payout_amount <= 1000 THEN '500-1000'
        WHEN r.payout_amount <= 2000 THEN '1000-2000'
        ELSE '2000以上'
    END
ORDER BY MIN(r.payout_amount);

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
    DATE(r.created_at) AS '日期',
    COUNT(*) AS '审核数量',
    SUM(CASE WHEN r.audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过数',
    SUM(CASE WHEN r.audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝数',
    SUM(r.payout_amount) AS '赔付金额'
FROM ai_review_result r
GROUP BY DATE(r.created_at)
ORDER BY DATE(r.created_at) DESC
LIMIT 30;

-- 19. 按月份统计
SELECT
    DATE_FORMAT(r.created_at, '%Y-%m') AS '月份',
    COUNT(*) AS '审核数量',
    SUM(r.payout_amount) AS '赔付金额',
    ROUND(AVG(f.delay_duration_minutes), 0) AS '平均延误(分)'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
GROUP BY DATE_FORMAT(r.created_at, '%Y-%m')
ORDER BY DATE_FORMAT(r.created_at, '%Y-%m') DESC;

-- 20. 按小时统计（查看审核高峰时段）
SELECT
    HOUR(r.created_at) AS '小时',
    COUNT(*) AS '审核数量'
FROM ai_review_result r
GROUP BY HOUR(r.created_at)
ORDER BY HOUR(r.created_at);

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
    r.forceid AS '案件ID',
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额',
    r.audit_time AS '审核时间'
FROM ai_review_result r
WHERE r.forwarded_to_frontend = 0 AND r.audit_result IS NOT NULL
ORDER BY r.audit_time DESC;

-- 23. 推送失败的案件
SELECT
    r.forceid AS '案件ID',
    r.audit_time AS '审核时间'
FROM ai_review_result r
WHERE r.forwarded_to_frontend = 1
ORDER BY r.forwarded_at DESC;

-- ============================================
-- 九、综合报表查询
-- ============================================

-- 24. 审核汇总报表（近30天）
SELECT
    DATE(r.created_at) AS '日期',
    COUNT(*) AS '总案件数',
    SUM(CASE WHEN r.audit_result = 'approved' THEN 1 ELSE 0 END) AS '通过',
    SUM(CASE WHEN r.audit_result = 'rejected' THEN 1 ELSE 0 END) AS '拒绝',
    SUM(CASE WHEN r.is_additional = 'Y' THEN 1 ELSE 0 END) AS '需补件',
    ROUND(AVG(f.delay_duration_minutes), 0) AS '平均延误(分)',
    SUM(r.payout_amount) AS '总赔付'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
GROUP BY DATE(r.created_at)
ORDER BY DATE(r.created_at) DESC;

-- 25. 案件详情查询（全部字段）
SELECT
    r.forceid AS '案件ID',
    r.claim_id AS '案件编号',
    r.claim_type AS '案件类型',
    r.benefit_name AS '险种名称',
    r.applicant_name AS '申请人',
    r.insured_name AS '被保险人',
    r.passenger_id_type AS '证件类型',
    r.passenger_id_number AS '证件号码',
    r.policy_no AS '保单号',
    r.insurer AS '保险公司',
    r.policy_effective_date AS '保单生效',
    r.policy_expiry_date AS '保单截止',
    f.flight_no AS '航班号',
    f.operating_carrier AS '承运人',
    f.dep_iata AS '出发IATA',
    f.arr_iata AS '到达IATA',
    f.dep_city AS '出发城市',
    f.arr_city AS '目的城市',
    f.planned_dep_time AS '计划起飞',
    f.actual_dep_time AS '实际起飞',
    f.planned_arr_time AS '计划到达',
    f.actual_arr_time AS '实际到达',
    f.delay_duration_minutes AS '延误时长(分)',
    f.delay_reason AS '延误原因',
    f.delay_type AS '延误类型',
    r.audit_result AS '审核结果',
    r.audit_status AS '审核状态',
    r.confidence_score AS '置信度(%)',
    r.audit_time AS '审核时间',
    r.auditor AS '审核员',
    r.payout_amount AS '赔付金额',
    r.payout_currency AS '币种',
    r.insured_amount AS '保额',
    r.remaining_coverage AS '剩余保额',
    r.is_additional AS '需补件',
    r.supplementary_reason AS '补件原因',
    r.remark AS '审核备注',
    r.decision_reason AS '核赔意见',
    r.identity_match AS '身份匹配',
    r.threshold_met AS '达到门槛',
    r.exclusion_triggered AS '触发免责',
    r.exclusion_reason AS '免责原因',
    r.forwarded_to_frontend AS '已推送',
    r.forwarded_at AS '推送时间',
    r.created_at AS '创建时间',
    r.updated_at AS '更新时间',
    b.baggage_receipt_time AS '行李签收时间',
    b.baggage_delay_hours AS '行李延误(小时)',
    b.delay_tier AS '行李延误档位',
    b.pir_no AS 'PIR编号'
FROM ai_review_result r
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
LEFT JOIN ai_baggage_delay_data b ON r.forceid = b.forceid
WHERE r.forceid = 'YOUR_FORCEID'
LIMIT 1;

-- ============================================
-- 十、关联查询
-- ============================================

-- 26. 关联案件状态表
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    f.flight_no AS '航班号',
    r.audit_result AS '审核结果',
    s.current_status AS '流程状态',
    s.download_status AS '下载状态',
    s.review_status AS '审核状态'
FROM ai_review_result r
LEFT JOIN ai_claim_status s ON r.forceid = s.forceid
LEFT JOIN ai_flight_delay_data f ON r.forceid = f.forceid
ORDER BY r.created_at DESC
LIMIT 50;

-- 27. 关联补件记录表
SELECT
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
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
    '有申请人姓名',
    SUM(CASE WHEN applicant_name IS NOT NULL AND applicant_name != '' THEN 1 ELSE 0 END)
FROM ai_review_result
UNION ALL
SELECT
    '有险种名称',
    SUM(CASE WHEN benefit_name IS NOT NULL AND benefit_name != '' THEN 1 ELSE 0 END)
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
    r.forceid AS '案件ID',
    r.applicant_name AS '申请人',
    r.audit_result AS '审核结果',
    r.payout_amount AS '赔付金额'
FROM ai_review_result r
WHERE r.audit_result = 'approved' AND (r.payout_amount IS NULL OR r.payout_amount = 0);
