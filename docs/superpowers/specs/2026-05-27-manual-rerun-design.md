# 人工变更触发 AI 自动重审 — 设计文档

> 日期：2026-05-27 | 状态：已批准 | 触发方式：sync_manual_status.py 即时标记 + scheduler 轮询消费

## 1. 需求

人工在外部系统（Salesforce）修改案件结论后，系统自动触发 AI 重新审核该案件。重审结论覆盖 `ai_review_result` 中对应字段，旧版本写入 `ai_review_history` 保留完整事件时间线。

### 约束条件

- **即时触发**：人工改完结论后，sync 脚本检测到变化即入队，scheduler 下次轮询即拉走
- **不限制次数**：人工改多少次就重审多少次
- **完整留痕**：人工改结论写一次 history，AI 重审前写一次 history（旧版快照），重审后再写一次（新版快照），共 3 次写入
- **解耦**：不影响现有 `ai_claim_status` 状态机和 `ai_review_result` 表结构

## 2. 架构

```
外部 Salesforce API (Rest_AI_CLaim_Result)
  → sync_manual_status.py 检测 Final_Status 变化
    → UPDATE ai_review_result.manual_status/manual_conclusion
    → INSERT ai_review_history (review_type='manual', 人工变更事件)
    → INSERT ai_rerun_queue (新表)
  → review_scheduler 每轮查 ai_rerun_queue
    → 对每个 forceid:
        → INSERT ai_review_history (review_type='ai', 旧版快照)
        → 执行 AI Pipeline（新建 reviewer 实例）
        → UPSERT ai_review_result（覆盖 AI 字段）
        → INSERT ai_review_history (review_type='ai', 新版快照)
        → 推前端
        → DELETE ai_rerun_queue 对应行
```

## 3. 新表结构

### ai_rerun_queue

```sql
CREATE TABLE IF NOT EXISTS ai_rerun_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    forceid VARCHAR(64) NOT NULL COMMENT '案件唯一标识',
    triggered_by VARCHAR(32) DEFAULT 'manual_status_change' COMMENT '触发来源',
    rerun_status ENUM('pending', 'processing', 'completed', 'failed') DEFAULT 'pending',
    retry_count INT DEFAULT 0 COMMENT '重审尝试次数',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_forceid_status (forceid, rerun_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI重审触发队列表';
```

### ai_review_history 字段扩展

需要新增两个字段以支持重审溯源：

```sql
ALTER TABLE ai_review_history
    ADD COLUMN IF NOT EXISTS triggered_by VARCHAR(32) NULL COMMENT '触发来源: manual_status_change / rerun',
    ADD COLUMN IF NOT EXISTS rerun_queue_id INT NULL COMMENT '关联的重审队列ID';
```

对应修改 `app/db/models.py` 中 `ReviewHistoryRecord` dataclass，新增：
```python
triggered_by: Optional[str] = None
rerun_queue_id: Optional[int] = None
```

对应修改 `app/db/history_helpers.py` 中写入函数，接受这两个新参数。

## 4. 核心改动清单

| # | 改动 | 文件 | 说明 |
|---|------|------|------|
| 1 | 新建迁移脚本 | `scripts/db/migrations/013_create_rerun_queue.sql` | 建 `ai_rerun_queue` + `ai_review_history` 加列 |
| 2 | 新 Dataclass | `app/db/models.py` | `RerunQueue` + `ReviewHistoryRecord` 加两个字段 |
| 3 | 新 DAO | `app/db/database.py` | `RerunQueueDAO`：enqueue, dequeue_pending, complete, fail |
| 4 | 同步脚本改造 | `scripts/sync_manual_status.py` | 检测变化时 INSERT queue + 写 history |
| 5 | 调度器改造 | `app/scheduler/review_scheduler.py` | 每轮查 queue 表消费 |
| 6 | 重审逻辑 | `app/scheduler/review_scheduler.py` | 快照旧行 → 重审 → 写新行 → 删 queue |
| 7 | history_helpers 扩展 | `app/db/history_helpers.py` | `write_ai_snapshot()` 接受新参数 |
| 8 | 测试 | `tests/test_rerun/` | DAO + 集成测试 |

## 5. 数据流详情

### 5.1 人工变更事件链

```python
# sync_manual_status.py — update_row() 函数内
# 1. 捕获旧值
old_manual_row = cur.fetchone()  # SELECT manual_status, manual_conclusion

# 2. UPDATE 新值
cur.execute("UPDATE ai_review_result SET manual_status=%s, manual_conclusion=%s, ...")

# 3. 写 history（人工变更事件）
write_manual_history_if_changed(conn, forceid, new_status, new_conclusion,
                                 old_values=old_manual_row)

# 4. INSERT queue（如果确实有变化）
if manual_status_changed:
    cur.execute(
        "INSERT INTO ai_rerun_queue (forceid, triggered_by) VALUES (%s, 'manual_status_change')",
        (forceid,)
    )
conn.commit()
```

### 5.2 AI 重审事件链

```python
# review_scheduler.py — process_pending_reviews() 内

# 1. 查 queue
queue_items = await rerun_queue_dao.dequeue_pending(limit=limit)

# 2. 对每个 forceid
for item in queue_items:
    forceid = item.forceid
    
    # 2a. 标记为 processing
    await rerun_queue_dao.mark_processing(item.id)
    
    # 2b. 快照旧行到 history
    old_row = await review_result_dao.get_result_by_forceid(forceid)
    if old_row:
        await history_dao.write_snapshot(
            forceid=forceid,
            review_type='ai',
            snapshot_json=json.dumps(old_row.to_dict()),
            triggered_by='rerun',
            rerun_queue_id=item.id,
        )
    
    # 2c. 执行 AI Pipeline（新建 reviewer 实例，避免竞态）
    reviewer = AIClaimReviewer()
    result = await review_claim_async(reviewer, claim_folder, policy_terms, ...)
    
    # 2d. UPSERT ai_review_result
    await review_result_dao.create_or_update_result(result)
    
    # 2e. 写 history（新版快照）
    await history_dao.write_snapshot(
        forceid=forceid,
        review_type='ai',
        snapshot_json=json.dumps(result),
        triggered_by='rerun',
        rerun_queue_id=item.id,
    )
    
    # 2f. 推前端
    await push_to_frontend(result, session)
    
    # 2g. 标记完成
    await rerun_queue_dao.complete(item.id)
```

## 6. 错误处理

| 场景 | 处理方式 |
|------|---------|
| AI 重审失败 | `rerun_status='failed'`, `retry_count++`，不删除行 |
| retry_count > 5 | `rerun_status='completed'`，不再重试 |
| sync 脚本写 queue 失败 | try/except 吞掉，只记录日志，不阻塞主流程 |
| 重审时找不到案件目录 | 标记 failed，下轮重试 |
| 同一 forceid 多次入队 | 多行共存，按 created_at 顺序逐条消费 |
| 重审中 scheduler 重启 | processing 状态行下次不被 dequeue（有超时回退机制：processing > 30min → 重置为 pending） |

## 7. 测试策略

| 测试类型 | 内容 | 位置 |
|---------|------|------|
| 单元测试 | `RerunQueueDAO` 的 enqueue/dequeue/complete/fail | `tests/test_rerun/test_dao.py` |
| 单元测试 | `history_helpers.write_snapshot()` 序列化正确性 | `tests/test_rerun/test_history.py` |
| 集成测试 | sync → queue INSERT → scheduler 消费 → history 写入完整链路 | `tests/test_rerun/test_integration.py` |
| 边界测试 | 重复触发、AI 失败重试、retry_count 超限、无案件目录 | `tests/test_rerun/test_edge_cases.py` |

## 8. 回滚方案

如果新机制导致问题：
1. 注释掉 `sync_manual_status.py` 中的 `INSERT ai_rerun_queue` 行
2. `review_scheduler` 继续运行原有的 `process_pending_reviews` 逻辑（只查 `ai_claim_status`）
3. 不清理 `ai_rerun_queue` 表数据，恢复后可继续消费
4. 不修改任何现有表结构（新表独立存在，删除即可）
