-- 数据库结构改进 - 第6阶段：历史对比视图
-- 版本: 012
-- 说明: 创建 v_ai_vs_manual_comparison 视图，方便查询 AI 历史版本与当前人工状态的对比

-- ============================================
-- Step 1: 创建 AI vs 人工对比视图
-- ============================================
CREATE OR REPLACE VIEW v_ai_vs_manual_comparison AS
SELECT
    h.id AS history_id,
    h.forceid AS '案件ID',
    h.claim_id AS '案件编号',
    h.benefit_name AS '险种',
    h.review_type AS '记录类型',
    h.audit_result AS 'AI审核结果',
    h.audit_status AS 'AI审核状态',
    h.confidence_score AS 'AI置信度',
    h.payout_amount AS 'AI赔付金额',
    h.identity_match AS 'AI身份匹配',
    h.threshold_met AS 'AI门槛检查',
    h.exclusion_triggered AS 'AI免责触发',
    h.manual_status AS '快照时人工状态',
    h.manual_conclusion AS '快照时人工结论',
    r.audit_result AS '当前AI结果',
    r.manual_status AS '当前人工状态',
    r.manual_conclusion AS '当前人工结论',
    r.first_ai_audit_result AS '首次AI结果',
    r.first_ai_manual_status AS '首次时人工状态',
    CASE
        WHEN h.audit_result IS NULL THEN 'N/A'
        WHEN h.manual_status IS NULL THEN '仅AI'
        WHEN h.audit_result = r.audit_result AND h.manual_status = r.manual_status THEN '一致'
        WHEN h.audit_result != r.audit_result THEN 'AI结论变更'
        ELSE '状态变更'
    END AS '对比标志',
    h.audit_time AS '审核时间',
    h.created_at AS '记录时间'
FROM ai_review_history h
LEFT JOIN ai_review_result r ON h.forceid = r.forceid
WHERE h.review_type = 'ai';

-- ============================================
-- Step 2: 创建历史统计视图
-- ============================================
CREATE OR REPLACE VIEW v_history_stats AS
SELECT
    COUNT(DISTINCT forceid) AS total_cases_with_history,
    SUM(CASE WHEN review_type = 'ai' THEN 1 ELSE 0 END) AS total_ai_versions,
    SUM(CASE WHEN review_type = 'manual' THEN 1 ELSE 0 END) AS total_manual_updates
FROM ai_review_history;

SELECT 'Stage 6 migration completed: history views created' AS status;
