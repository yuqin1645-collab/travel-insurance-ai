-- 数据库结构改进 - 第2阶段：数据迁移（主表 → 子表）
-- 版本: 008
-- 说明: 将已有字段从 ai_review_result 迁移到对应的子表
-- 前提: 007 迁移已执行（子表和新列已创建）

-- ============================================
-- Step 4: 从 ai_review_result 迁移航班字段 → ai_flight_delay_data
-- 仅迁移 claim_type = 'flight_delay' 或 benefit_name 不含"行李"的记录
-- ============================================

INSERT INTO ai_flight_delay_data (
    forceid,
    flight_no, operating_carrier,
    dep_iata, arr_iata, dep_city, arr_city,
    planned_dep_time, planned_arr_time, actual_dep_time, actual_arr_time,
    alt_dep_time, alt_arr_time, alt_flight_no, alt_dep_iata, alt_arr_iata,
    avi_status, avi_planned_dep, avi_planned_arr, avi_actual_dep, avi_actual_arr,
    avi_alt_flight_no, avi_alt_planned_dep, avi_alt_actual_dep, avi_alt_actual_arr,
    flight_scenario, rebooking_count,
    is_connecting, total_segments, origin_iata, destination_iata, missed_connection,
    delay_duration_minutes, delay_reason, delay_type, delay_calc_from, delay_calc_to
)
SELECT
    r.forceid,
    r.flight_no, r.operating_carrier,
    r.dep_iata, r.arr_iata, r.dep_city, r.arr_city,
    r.planned_dep_time, r.planned_arr_time, r.actual_dep_time, r.actual_arr_time,
    r.alt_dep_time, r.alt_arr_time, r.alt_flight_no, r.alt_dep_iata, r.alt_arr_iata,
    r.avi_status, r.avi_planned_dep, r.avi_planned_arr, r.avi_actual_dep, r.avi_actual_arr,
    r.avi_alt_flight_no, r.avi_alt_planned_dep, r.avi_alt_actual_dep, r.avi_alt_actual_arr,
    r.flight_scenario, r.rebooking_count,
    r.is_connecting, r.total_segments, r.origin_iata, r.destination_iata, r.missed_connection,
    r.delay_duration_minutes, r.delay_reason, r.delay_type, r.delay_calc_from, r.delay_calc_to
FROM ai_review_result r
WHERE (r.claim_type = 'flight_delay' OR (r.claim_type IS NULL AND (r.benefit_name IS NULL OR r.benefit_name NOT LIKE '%行李%')))
  AND r.benefit_name NOT LIKE '%行李%'
  AND NOT EXISTS (SELECT 1 FROM ai_flight_delay_data f WHERE f.forceid = r.forceid);

SELECT CONCAT('Step 4 done: migrated ', ROW_COUNT(), ' flight delay records') AS status;

-- ============================================
-- Step 5: 从 ai_review_result 迁移行李字段 → ai_baggage_delay_data
-- 仅迁移 claim_type = 'baggage_delay' 或 benefit_name 含"行李"的记录
-- ============================================

INSERT INTO ai_baggage_delay_data (
    forceid,
    baggage_receipt_time, baggage_delay_hours,
    has_baggage_receipt_proof, has_baggage_delay_proof, has_baggage_tag, pir_no
)
SELECT
    r.forceid,
    r.baggage_receipt_time, r.baggage_delay_hours,
    r.has_baggage_receipt_proof, r.has_baggage_delay_proof, r.has_baggage_tag_proof, r.pir_no
FROM ai_review_result r
WHERE (r.claim_type = 'baggage_delay' OR r.benefit_name LIKE '%行李%')
  AND NOT EXISTS (SELECT 1 FROM ai_baggage_delay_data b WHERE b.forceid = r.forceid);

SELECT CONCAT('Step 5 done: migrated ', ROW_COUNT(), ' baggage delay records') AS status;

SELECT 'Stage 2 migration completed: data moved from main table to sub-tables' AS status;
