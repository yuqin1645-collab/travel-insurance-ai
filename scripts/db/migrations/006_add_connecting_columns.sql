-- Migration 006: 联程支持
-- 1. ai_review_result 新增联程汇总标量字段（去掉 JSON 方案）
-- 2. 新建 ai_review_segments 联程航段子表
-- 执行时间: 2026-04-20

-- =====================================================================
-- Part 1: ai_review_result 新增联程汇总标量字段
-- =====================================================================

-- 是否联程（1=联程，0=直飞，NULL=未判断）
ALTER TABLE ai_review_result
    ADD COLUMN is_connecting TINYINT(1) DEFAULT NULL
    COMMENT '是否联程（1=联程，0=直飞）' AFTER rebooking_count;

-- 联程总段数（直飞=1，两段联程=2）
ALTER TABLE ai_review_result
    ADD COLUMN total_segments TINYINT DEFAULT NULL
    COMMENT '联程总段数（直飞=1）' AFTER is_connecting;

-- 整个行程出发机场（联程首段起飞地；直飞与 dep_iata 相同）
ALTER TABLE ai_review_result
    ADD COLUMN origin_iata VARCHAR(8) DEFAULT NULL
    COMMENT '全程出发机场IATA（联程首段）' AFTER total_segments;

-- 整个行程最终目的地（联程末段落地；直飞与 arr_iata 相同）
ALTER TABLE ai_review_result
    ADD COLUMN destination_iata VARCHAR(8) DEFAULT NULL
    COMMENT '全程目的地IATA（联程末段）' AFTER origin_iata;

-- 是否因前段延误导致误机（接驳失误）
ALTER TABLE ai_review_result
    ADD COLUMN missed_connection TINYINT(1) DEFAULT NULL
    COMMENT '是否联程接驳失误（前段延误导致错过后段）' AFTER destination_iata;

-- 索引
CREATE INDEX idx_is_connecting     ON ai_review_result (is_connecting);
CREATE INDEX idx_origin_dest       ON ai_review_result (origin_iata, destination_iata);
CREATE INDEX idx_missed_connection ON ai_review_result (missed_connection);

-- =====================================================================
-- Part 2: 新建 ai_review_segments 联程航段子表
-- =====================================================================
CREATE TABLE IF NOT EXISTS ai_review_segments (
    id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    forceid          VARCHAR(64)     NOT NULL                COMMENT '关联 ai_review_result.forceid',

    -- 票号与航段序号
    ticket_no        VARCHAR(64)     DEFAULT NULL            COMMENT '票号',
    segment_no       TINYINT         NOT NULL DEFAULT 1      COMMENT '航段序号（1起算）',

    -- 航班号与航线
    flight_no        VARCHAR(32)     DEFAULT NULL            COMMENT '本段航班号',
    dep_iata         VARCHAR(8)      DEFAULT NULL            COMMENT '本段起飞机场IATA',
    arr_iata         VARCHAR(8)      DEFAULT NULL            COMMENT '本段到达机场IATA',
    origin_iata      VARCHAR(8)      DEFAULT NULL            COMMENT '全程始发地IATA（冗余，方便按段查询）',
    destination_iata VARCHAR(8)      DEFAULT NULL            COMMENT '全程目的地IATA（冗余）',

    -- 计划时间（材料/保单）
    planned_dep      DATETIME        DEFAULT NULL            COMMENT '计划起飞时间',
    planned_arr      DATETIME        DEFAULT NULL            COMMENT '计划到达时间',

    -- 实际时间（飞常准）
    actual_dep       DATETIME        DEFAULT NULL            COMMENT '飞常准实际起飞',
    actual_arr       DATETIME        DEFAULT NULL            COMMENT '飞常准实际到达',

    -- 延误计算
    delay_min        INT             DEFAULT NULL            COMMENT '本段延误分钟',
    avi_status       VARCHAR(32)     DEFAULT NULL            COMMENT '飞常准航班状态（正常/延误/取消）',

    -- 标志位
    is_triggered     TINYINT(1)      DEFAULT NULL            COMMENT '是否触发延误险赔付的那段（1=是）',
    is_connecting    TINYINT(1)      DEFAULT NULL            COMMENT '是否联程（与主表一致，冗余）',
    missed_connect   TINYINT(1)      DEFAULT NULL            COMMENT '本段是否因前段延误而误机',

    created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_seg_forceid    (forceid),
    KEY idx_seg_flight_no  (flight_no),
    KEY idx_seg_is_triggered (is_triggered),
    KEY idx_seg_dep_iata   (dep_iata),
    KEY idx_seg_arr_iata   (arr_iata),
    CONSTRAINT fk_seg_forceid FOREIGN KEY (forceid)
        REFERENCES ai_review_result (forceid)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='联程各航段详情（一条主记录对应多行）';
