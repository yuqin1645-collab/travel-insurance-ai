-- Migration 013: 创建 AI 重审队列表 + 历史表加列
-- 用途：人工修改案件结论后，自动触发 AI 重新审核

-- 1. 创建重审队列表
CREATE TABLE IF NOT EXISTS ai_rerun_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一标识',
    triggered_by VARCHAR(32) DEFAULT 'manual_status_change' COMMENT '触发来源: manual_status_change / system_retry / force',
    rerun_status ENUM('pending', 'processing', 'completed', 'failed', 'abandoned') DEFAULT 'pending',
    retry_count INT DEFAULT 0 COMMENT '重审尝试次数',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_forceid_status (forceid, rerun_status),
    INDEX idx_status_created (rerun_status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI重审触发队列表';

-- 2. ai_review_history 表加列（支持重审溯源）
-- MySQL 不支持 ADD COLUMN IF NOT EXISTS，使用存储过程实现幂等

-- 添加 triggered_by 列
SET @col_exists = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'ai_review_history'
    AND COLUMN_NAME = 'triggered_by'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE ai_review_history ADD COLUMN triggered_by VARCHAR(32) NULL COMMENT ''触发来源'' AFTER snapshot_json',
    'SELECT ''Column triggered_by already exists'' AS msg'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 添加 rerun_queue_id 列
SET @col_exists2 = (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME = 'ai_review_history'
    AND COLUMN_NAME = 'rerun_queue_id'
);
SET @sql2 = IF(@col_exists2 = 0,
    'ALTER TABLE ai_review_history ADD COLUMN rerun_queue_id INT NULL COMMENT ''关联的重审队列ID'' AFTER triggered_by',
    'SELECT ''Column rerun_queue_id already exists'' AS msg'
);
PREPARE stmt2 FROM @sql2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;
