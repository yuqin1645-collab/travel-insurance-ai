-- AI审核结果表（用于同步到nlpp等上游系统）
-- 执行前请确保已选择数据库: USE ai;

CREATE TABLE IF NOT EXISTS ai_review_result (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一ID',
    claim_id VARCHAR(64) NULL COMMENT 'ClaimId(与上游一致)',
    remark VARCHAR(2000) NOT NULL DEFAULT '' COMMENT '审核结论摘要',
    is_additional CHAR(1) NOT NULL DEFAULT 'Y' COMMENT 'Y=需补件, N=最终结论',
    key_conclusions LONGTEXT COMMENT '各核对点结论(JSON)',
    raw_result LONGTEXT COMMENT '完整原始JSON',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_forceid (forceid),
    KEY idx_is_additional (is_additional),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI理赔审核结果';
