CREATE TABLE IF NOT EXISTS ai_review_history (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL,
    claim_id VARCHAR(64) DEFAULT NULL,
    benefit_name VARCHAR(64) DEFAULT NULL,
    review_type ENUM('ai', 'manual') NOT NULL,
    audit_result VARCHAR(32) DEFAULT NULL,
    audit_status VARCHAR(32) DEFAULT NULL,
    confidence_score DECIMAL(5,2) DEFAULT NULL,
    payout_amount DECIMAL(10,2) DEFAULT NULL,
    identity_match CHAR(1) DEFAULT NULL,
    threshold_met CHAR(1) DEFAULT NULL,
    exclusion_triggered CHAR(1) DEFAULT NULL,
    manual_status VARCHAR(32) DEFAULT NULL,
    manual_conclusion TEXT DEFAULT NULL,
    ai_model_version VARCHAR(32) DEFAULT NULL,
    pipeline_version VARCHAR(32) DEFAULT NULL,
    rule_ids_hit TEXT DEFAULT NULL,
    audit_time DATETIME DEFAULT NULL,
    snapshot_json LONGTEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_forceid (forceid),
    KEY idx_review_type (review_type),
    KEY idx_forceid_type (forceid, review_type),
    KEY idx_audit_time (audit_time),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE ai_review_result ADD COLUMN first_ai_audit_result VARCHAR(32) DEFAULT NULL AFTER audit_time;
ALTER TABLE ai_review_result ADD COLUMN first_ai_audit_status VARCHAR(32) DEFAULT NULL AFTER first_ai_audit_result;
ALTER TABLE ai_review_result ADD COLUMN first_ai_audit_time DATETIME DEFAULT NULL AFTER first_ai_audit_status;
ALTER TABLE ai_review_result ADD COLUMN first_ai_confidence DECIMAL(5,2) DEFAULT NULL AFTER first_ai_audit_time;
ALTER TABLE ai_review_result ADD COLUMN first_ai_manual_status VARCHAR(32) DEFAULT NULL AFTER first_ai_confidence;
ALTER TABLE ai_review_result ADD COLUMN first_ai_manual_conclusion TEXT DEFAULT NULL AFTER first_ai_manual_status;
