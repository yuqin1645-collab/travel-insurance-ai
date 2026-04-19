-- 航班延误AI审核系统 - 航班详细字段扩展
-- 版本: 005
-- 说明：重新设计航班信息字段，区分原航班/实际乘坐/飞常准数据，增加场景标签和延误计算追溯

-- ============================================
-- 实际乘坐航班信息（改签/替代）
-- ============================================
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS alt_flight_no VARCHAR(32) NULL COMMENT '被保险人实际乘坐的改签航班号' AFTER alt_arr_time;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS alt_dep_iata VARCHAR(8) NULL COMMENT '实际乘坐航班出发机场IATA' AFTER alt_flight_no;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS alt_arr_iata VARCHAR(8) NULL COMMENT '实际乘坐航班到达机场IATA' AFTER alt_dep_iata;

-- ============================================
-- 航班场景标签
-- ============================================
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS flight_scenario VARCHAR(32) NULL COMMENT '航班场景: direct/connecting/rebooking/multi_rebooking/cancelled_nofly' AFTER alt_arr_iata;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS rebooking_count TINYINT NULL DEFAULT 0 COMMENT '改签次数（0=无改签）' AFTER flight_scenario;

-- ============================================
-- 飞常准查原航班独立字段
-- ============================================
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_status VARCHAR(32) NULL COMMENT '飞常准原航班状态（正常/延误/取消）' AFTER rebooking_count;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_planned_dep DATETIME NULL COMMENT '飞常准：原航班计划起飞' AFTER avi_status;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_planned_arr DATETIME NULL COMMENT '飞常准：原航班计划到达' AFTER avi_planned_dep;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_actual_dep DATETIME NULL COMMENT '飞常准：原航班实际起飞' AFTER avi_planned_arr;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_actual_arr DATETIME NULL COMMENT '飞常准：原航班实际到达' AFTER avi_actual_dep;

-- ============================================
-- 飞常准查替代航班独立字段
-- ============================================
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_alt_flight_no VARCHAR(32) NULL COMMENT '飞常准查到的替代航班号' AFTER avi_actual_arr;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_alt_planned_dep DATETIME NULL COMMENT '飞常准：替代航班计划起飞' AFTER avi_alt_flight_no;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_alt_actual_dep DATETIME NULL COMMENT '飞常准：替代航班实际起飞' AFTER avi_alt_planned_dep;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS avi_alt_actual_arr DATETIME NULL COMMENT '飞常准：替代航班实际到达' AFTER avi_alt_actual_dep;

-- ============================================
-- 延误计算追溯
-- ============================================
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS delay_calc_from VARCHAR(64) NULL COMMENT '延误计算起点字段名（如 avi_planned_dep / planned_dep_time）' AFTER avi_alt_actual_arr;
ALTER TABLE ai_review_result ADD COLUMN IF NOT EXISTS delay_calc_to VARCHAR(64) NULL COMMENT '延误计算终点字段名（如 avi_actual_arr / alt_arr_time）' AFTER delay_calc_from;

-- ============================================
-- 新增索引
-- ============================================
ALTER TABLE ai_review_result ADD INDEX IF NOT EXISTS idx_flight_scenario (flight_scenario);
ALTER TABLE ai_review_result ADD INDEX IF NOT EXISTS idx_avi_status (avi_status);
ALTER TABLE ai_review_result ADD INDEX IF NOT EXISTS idx_alt_flight_no (alt_flight_no);

SELECT 'Migration 005 completed - flight detail columns added' AS status;
