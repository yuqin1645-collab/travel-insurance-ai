-- 数据库结构改进 - 第3阶段：清理主表已迁移的列
-- 版本: 009
-- 说明: 从 ai_review_result 删除已迁移到子表的列
-- ⚠ 危险操作：执行前请确认 007 和 008 迁移已成功完成，且子表数据完整！
-- 注意: MySQL 不支持 DROP COLUMN IF EXISTS，此脚本由 Python 逐列执行并处理 1091 错误
-- ============================================
-- Step 6: 从 ai_review_result 删除已迁移的列
-- ============================================

-- 航班基础信息（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN flight_no;
ALTER TABLE ai_review_result DROP COLUMN operating_carrier;

-- 航线信息（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN dep_iata;
ALTER TABLE ai_review_result DROP COLUMN arr_iata;
ALTER TABLE ai_review_result DROP COLUMN dep_city;
ALTER TABLE ai_review_result DROP COLUMN arr_city;

-- 原航班时间（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN planned_dep_time;
ALTER TABLE ai_review_result DROP COLUMN planned_arr_time;
ALTER TABLE ai_review_result DROP COLUMN actual_dep_time;
ALTER TABLE ai_review_result DROP COLUMN actual_arr_time;

-- 改签信息（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN alt_dep_time;
ALTER TABLE ai_review_result DROP COLUMN alt_arr_time;
ALTER TABLE ai_review_result DROP COLUMN alt_flight_no;
ALTER TABLE ai_review_result DROP COLUMN alt_dep_iata;
ALTER TABLE ai_review_result DROP COLUMN alt_arr_iata;

-- 飞常准原航班字段（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN avi_status;
ALTER TABLE ai_review_result DROP COLUMN avi_planned_dep;
ALTER TABLE ai_review_result DROP COLUMN avi_planned_arr;
ALTER TABLE ai_review_result DROP COLUMN avi_actual_dep;
ALTER TABLE ai_review_result DROP COLUMN avi_actual_arr;

-- 飞常准替代航班字段（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN avi_alt_flight_no;
ALTER TABLE ai_review_result DROP COLUMN avi_alt_planned_dep;
ALTER TABLE ai_review_result DROP COLUMN avi_alt_actual_dep;
ALTER TABLE ai_review_result DROP COLUMN avi_alt_actual_arr;

-- 航班场景/联程（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN flight_scenario;
ALTER TABLE ai_review_result DROP COLUMN rebooking_count;
ALTER TABLE ai_review_result DROP COLUMN is_connecting;
ALTER TABLE ai_review_result DROP COLUMN total_segments;
ALTER TABLE ai_review_result DROP COLUMN origin_iata;
ALTER TABLE ai_review_result DROP COLUMN destination_iata;
ALTER TABLE ai_review_result DROP COLUMN missed_connection;

-- 延误计算（迁移到 ai_flight_delay_data）
ALTER TABLE ai_review_result DROP COLUMN delay_duration_minutes;
ALTER TABLE ai_review_result DROP COLUMN delay_reason;
ALTER TABLE ai_review_result DROP COLUMN delay_type;
ALTER TABLE ai_review_result DROP COLUMN delay_calc_from;
ALTER TABLE ai_review_result DROP COLUMN delay_calc_to;

-- 行李字段（迁移到 ai_baggage_delay_data）
ALTER TABLE ai_review_result DROP COLUMN baggage_receipt_time;
ALTER TABLE ai_review_result DROP COLUMN baggage_delay_hours;
ALTER TABLE ai_review_result DROP COLUMN has_baggage_delay_proof;
ALTER TABLE ai_review_result DROP COLUMN has_baggage_receipt_proof;
ALTER TABLE ai_review_result DROP COLUMN has_baggage_tag_proof;
ALTER TABLE ai_review_result DROP COLUMN pir_no;

-- 国家字段（数据错误，删除）
ALTER TABLE ai_review_result DROP COLUMN dep_country;
ALTER TABLE ai_review_result DROP COLUMN arr_country;

-- 死字段（从未被任何写入路径赋值）
ALTER TABLE ai_review_result DROP COLUMN payout_basis;
ALTER TABLE ai_review_result DROP COLUMN supplementary_deadline;
ALTER TABLE ai_review_result DROP COLUMN frontend_response;
ALTER TABLE ai_review_result DROP COLUMN metadata;
ALTER TABLE ai_review_result DROP COLUMN supplementary_count;

SELECT 'Stage 3 migration completed: migrated columns dropped from main table' AS status;
