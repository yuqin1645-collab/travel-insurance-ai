-- Migration 013: 创建 AI 重审队列表 + 历史表加列
-- 用途：人工修改案件结论后，自动触发 AI 重新审核

-- 1. 创建重审队列表
CREATE TABLE IF NOT EXISTS ai_rerun_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一标识',
    triggered_by VARCHAR(32) DEFAULT 'manual_status_change' COMMENT '触发来源: manual_status_change / system_retry / force',
    rerun_status ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
    retry_count INT DEFAULT 0 COMMENT '重审尝试次数',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_forceid_status (forceid, rerun_status),
    INDEX idx_status_created (rerun_status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI重审触发队列表';

-- 2. ai_review_history 表加列（支持重审溯源）
ALTER TABLE ai_review_history
    ADD COLUMN IF NOT EXISTS triggered_by VARCHAR(32) NULL COMMENT '触发来源',
    ADD COLUMN IF NOT EXISTS rerun_queue_id INT NULL COMMENT '关联的重审队列ID';
