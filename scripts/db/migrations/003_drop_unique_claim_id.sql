
-- 003: drop unique index uk_claim_id on ai_claim_status
-- Reason:
--   接口中同一个 claim_id(CaseNo) 可能对应多个 forceid（补件/重提/上游变更）。
--   保留 claim_id 唯一会导致下载调度阶段插入冲突并中断整批任务。

SET @db_name := DATABASE();

-- 1) 若存在唯一索引 uk_claim_id，则删除
SET @uk_exists := (
    SELECT COUNT(1)
    FROM information_schema.statistics
    WHERE table_schema = @db_name
      AND table_name = 'ai_claim_status'
      AND index_name = 'uk_claim_id'
);

SET @drop_sql := IF(
    @uk_exists > 0,
    'ALTER TABLE ai_claim_status DROP INDEX uk_claim_id',
    'SELECT ''uk_claim_id not exists'' AS msg'
);
PREPARE stmt_drop FROM @drop_sql;
EXECUTE stmt_drop;
DEALLOCATE PREPARE stmt_drop;

-- 2) 确保 claim_id 仍有普通索引，便于查询性能
SET @idx_exists := (
    SELECT COUNT(1)
    FROM information_schema.statistics
    WHERE table_schema = @db_name
      AND table_name = 'ai_claim_status'
      AND index_name = 'idx_claim_id'
);

SET @create_sql := IF(
    @idx_exists = 0,
    'ALTER TABLE ai_claim_status ADD INDEX idx_claim_id (claim_id)',
    'SELECT ''idx_claim_id exists'' AS msg'
);
PREPARE stmt_create FROM @create_sql;
EXECUTE stmt_create;
DEALLOCATE PREPARE stmt_create;

SELECT 'migration_003_done' AS status;

