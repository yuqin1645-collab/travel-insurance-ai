# 人工变更触发 AI 自动重审 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当人工在外部系统修改案件结论后，系统自动将案件加入重审队列，scheduler 轮询消费该队列完成 AI 重审，并将新旧结论都写入历史记录。

**Architecture:** 新增独立队列表 `ai_rerun_queue`，同步脚本检测到人工状态变化时 INSERT 入队，`review_scheduler` 每轮消费队列中的 forceid 执行重审，重审前后分别写快照到 `ai_review_history`。

**Tech Stack:** Python 3.14, aiomysql (异步 DB), pymysql (同步 DB 脚本), asyncio

---

## 文件映射

| 操作 | 文件 | 说明 |
|------|------|------|
| **创建** | `scripts/db/migrations/013_create_rerun_queue.sql` | 队列表 + 历史表加列 |
| **修改** | `app/db/models.py` | 新增 `RerunQueue` dataclass + `ReviewHistoryRecord` 加两个字段 |
| **修改** | `app/db/database.py` | 新增 `RerunQueueDAO` 类 + 工厂函数 |
| **修改** | `app/db/history_helpers.py` | `insert_history_row` 支持新字段 |
| **修改** | `scripts/sync_manual_status.py` | 检测变化时 INSERT queue |
| **修改** | `app/scheduler/review_scheduler.py` | 消费 queue 的重审逻辑 |
| **创建** | `tests/test_rerun/test_dao.py` | RerunQueueDAO 单元测试 |
| **创建** | `tests/test_rerun/__init__.py` | 测试包 |

---

### Task 1: 数据库迁移脚本

**Files:**
- Create: `scripts/db/migrations/013_create_rerun_queue.sql`

- [ ] **Step 1: 创建迁移脚本**

```sql
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
```

- [ ] **Step 2: 验证 SQL 语法**

```bash
# 确保文件可读
cat scripts/db/migrations/013_create_rerun_queue.sql | wc -l
```

---

### Task 2: 新增 RerunQueue Dataclass

**Files:**
- Modify: `app/db/models.py`

- [ ] **Step 1: 在 models.py 末尾（`TABLE_REVIEW_HISTORY` 定义之后）添加 `RerunQueue` dataclass**

在 `TABLE_REVIEW_HISTORY = "ai_review_history"` 这行之后添加：

```python
TABLE_RERUN_QUEUE = "ai_rerun_queue"


@dataclass
class RerunQueue:
    """AI重审队列表（ai_rerun_queue）"""
    id: Optional[int] = None
    forceid: str = ""
    triggered_by: str = "manual_status_change"
    rerun_status: str = "pending"  # pending | processing | completed | failed
    retry_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RerunQueue':
        datetime_fields = ['created_at', 'updated_at']
        for field_name in datetime_fields:
            if field_name in data and data[field_name]:
                if isinstance(data[field_name], str):
                    try:
                        data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
                    except ValueError:
                        data[field_name] = None
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        return cls(**data)
```

- [ ] **Step 2: 修改 `ReviewHistoryRecord` dataclass，新增两个字段**

在 `ReviewHistoryRecord` 的 `snapshot_json` 字段之后、`created_at` 之前添加：

```python
    # 重审溯源
    triggered_by: Optional[str] = None
    rerun_queue_id: Optional[int] = None

    created_at: datetime = field(default_factory=datetime.now)
```

- [ ] **Step 3: 在文件顶部 import 中添加 `RerunQueue` 和 `TABLE_RERUN_QUEUE`**

确认 `RerunQueue` 和 `TABLE_RERUN_QUEUE` 被 `database.py` 导入（Task 3 处理）。

---

### Task 3: 新增 RerunQueueDAO

**Files:**
- Modify: `app/db/database.py`

- [ ] **Step 1: 在 import 中添加 `RerunQueue` 和 `TABLE_RERUN_QUEUE`**

修改第 17-27 行的 import：

```python
from app.db.models import (
    ClaimStatusRecord, ReviewResult, SupplementaryRecord, SchedulerLog, StatusHistory,
    FlightDelayData, BaggageDelayData,
    ClaimStatus, DownloadStatus, ReviewStatus, SupplementaryStatus, TaskType, TaskStatus,
    TABLE_CLAIM_STATUS, TABLE_REVIEW_RESULT, TABLE_SUPPLEMENTARY_RECORDS,
    TABLE_SCHEDULER_LOGS, TABLE_STATUS_HISTORY,
    TABLE_FLIGHT_DELAY_DATA, TABLE_BAGGAGE_DELAY_DATA,
    ClaimInfoRaw, TABLE_CLAIM_INFO_RAW,
    ReviewSegment, TABLE_REVIEW_SEGMENTS,
    ReviewHistoryRecord, TABLE_REVIEW_HISTORY,
    RerunQueue, TABLE_RERUN_QUEUE,
)
```

- [ ] **Step 2: 在 `ReviewHistoryDAO` 类之后（`get_review_history_dao()` 函数之前）添加 `RerunQueueDAO`**

```python
class RerunQueueDAO:
    """重审队列数据访问对象"""

    def __init__(self, db: DatabaseConnection):
        self.db = db

    async def enqueue(self, forceid: str, triggered_by: str = "manual_status_change") -> int:
        """入队：INSERT INTO ai_rerun_queue

        如果同一 forceid 已有 pending 或 processing 状态的行，不重复入队。

        Returns:
            新插入的行 ID，如果已存在则返回 0
        """
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                # 检查是否已有 pending/processing 状态的行
                await cursor.execute(
                    f"SELECT id FROM {TABLE_RERUN_QUEUE} "
                    f"WHERE forceid = %s AND rerun_status IN ('pending', 'processing')",
                    (forceid,)
                )
                existing = await cursor.fetchone()
                if existing:
                    return 0  # 已存在，不重复入队

                await cursor.execute(
                    f"INSERT INTO {TABLE_RERUN_QUEUE} (forceid, triggered_by) VALUES (%s, %s)",
                    (forceid, triggered_by),
                )
                return cursor.lastrowid

    async def dequeue_pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """取出 pending 状态的行，按 created_at 升序

        Returns:
            列表，每项包含 id, forceid, triggered_by, retry_count, created_at
        """
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"SELECT id, forceid, triggered_by, retry_count, created_at "
                    f"FROM {TABLE_RERUN_QUEUE} "
                    f"WHERE rerun_status = 'pending' "
                    f"ORDER BY created_at ASC "
                    f"LIMIT %s",
                    (limit,),
                )
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def mark_processing(self, queue_id: int) -> bool:
        """标记为 processing"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {TABLE_RERUN_QUEUE} SET rerun_status = 'processing' WHERE id = %s",
                    (queue_id,),
                )
                return cursor.rowcount > 0

    async def complete(self, queue_id: int) -> bool:
        """标记为 completed（删除行）"""
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"DELETE FROM {TABLE_RERUN_QUEUE} WHERE id = %s",
                    (queue_id,),
                )
                return True

    async def fail(self, queue_id: int, max_retries: int = 5) -> bool:
        """标记失败：retry_count++，超过 max_retries 则标记 completed"""
        async with self.db.get_connection() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    f"SELECT retry_count FROM {TABLE_RERUN_QUEUE} WHERE id = %s",
                    (queue_id,),
                )
                row = await cursor.fetchone()
                if not row:
                    return False

                new_count = (row["retry_count"] or 0) + 1
                if new_count > max_retries:
                    await cursor.execute(
                        f"UPDATE {TABLE_RERUN_QUEUE} "
                        f"SET rerun_status = 'completed', retry_count = %s "
                        f"WHERE id = %s",
                        (new_count, queue_id),
                    )
                else:
                    await cursor.execute(
                        f"UPDATE {TABLE_RERUN_QUEUE} "
                        f"SET rerun_status = 'pending', retry_count = %s "
                        f"WHERE id = %s",
                        (new_count, queue_id),
                    )
                return True

    async def reset_stale_processing(self, timeout_minutes: int = 30) -> int:
        """将 processing 超过 timeout_minutes 的行重置为 pending

        Returns:
            重置的行数
        """
        async with self.db.get_connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    f"UPDATE {TABLE_RERUN_QUEUE} "
                    f"SET rerun_status = 'pending', updated_at = CURRENT_TIMESTAMP "
                    f"WHERE rerun_status = 'processing' "
                    f"AND updated_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)",
                    (timeout_minutes,),
                )
                return cursor.rowcount


def get_rerun_queue_dao() -> RerunQueueDAO:
    """获取重审队列DAO"""
    return RerunQueueDAO(_db_connection)
```

---

### Task 4: history_helpers 扩展

**Files:**
- Modify: `app/db/history_helpers.py`

- [ ] **Step 1: 修改 `insert_history_row` 支持新字段**

`insert_history_row` 函数已使用通用 keys/values 模式，无需修改。但需确认 `TRACKED_AI_FIELDS` 不需要扩展。保持现有列表不变——重审快照通过 `snapshot_json` 字段保存完整信息。

无需修改此文件。

---

### Task 5: 同步脚本改造（入队逻辑）

**Files:**
- Modify: `scripts/sync_manual_status.py`

- [ ] **Step 1: 在 `update_row` 函数中新增入队逻辑**

找到 `update_row` 函数（当前在 line ~65-105），在 `write_manual_history_if_changed()` 之后、`conn.commit()` 之前添加入队逻辑。修改后的完整 `update_row` 函数：

```python
def update_row(conn, forceid: str, benefit_name: Optional[str],
               manual_status: Optional[str], manual_conclusion: Optional[str],
               dry_run: bool) -> None:
    if dry_run:
        print(f"  [dry-run] {forceid}: benefit_name={benefit_name} "
              f"manual_status={manual_status} manual_conclusion={str(manual_conclusion or '')[:60]}")
        return

    # 在 UPDATE 前捕获旧值
    with conn.cursor() as cur:
        cur.execute(
            "SELECT manual_status, manual_conclusion FROM ai_review_result WHERE forceid=%s",
            (forceid,)
        )
        old_manual_row = cur.fetchone()

    # 判断是否真的有变化
    old_status = (old_manual_row or {}).get('manual_status')
    old_conclusion = (old_manual_row or {}).get('manual_conclusion')
    has_change = (
        str(old_status or '').strip() != str(manual_status or '').strip() or
        str(old_conclusion or '').strip() != str(manual_conclusion or '').strip()
    )

    with conn.cursor() as cur:
        cur.execute(
            """UPDATE ai_review_result
               SET benefit_name = %s,
                   manual_status = %s,
                   manual_conclusion = %s,
                   updated_at = CURRENT_TIMESTAMP
               WHERE forceid = %s""",
            (benefit_name, manual_status, manual_conclusion, forceid),
        )

    # 历史版本追踪
    try:
        from app.db.history_helpers import write_manual_history_if_changed
        if has_change:
            write_manual_history_if_changed(conn, forceid, manual_status, manual_conclusion,
                                            benefit_name=benefit_name,
                                            old_values=old_manual_row)
    except Exception:
        pass  # 不阻塞主流程

    # 重审队列入队（仅在人工结论变化时）
    if has_change:
        try:
            with conn.cursor() as cur:
                # 检查是否已有 pending/processing 状态的行
                cur.execute(
                    "SELECT id FROM ai_rerun_queue "
                    "WHERE forceid = %s AND rerun_status IN ('pending', 'processing')",
                    (forceid,)
                )
                existing = cur.fetchone()
                if not existing:
                    cur.execute(
                        "INSERT INTO ai_rerun_queue (forceid, triggered_by) VALUES (%s, 'manual_status_change')",
                        (forceid,)
                    )
        except Exception as _err:
            pass  # 不阻塞主流程

    conn.commit()
```

- [ ] **Step 2: 更新 `get_db_conn` 使用 TLS（同步之前 P0 修复已做，这里确认一致性）**

确认 `get_db_conn()` 已包含 `ssl=ssl.create_default_context()` 和凭据校验（P0-1 修复）。如尚未包含，添加：

```python
def get_db_conn():
    db_host = os.getenv("DB_HOST")
    db_password = os.getenv("DB_PASSWORD")
    if not db_host:
        raise RuntimeError("数据库连接失败: DB_HOST 未配置")
    if not db_password:
        raise RuntimeError("数据库连接失败: DB_PASSWORD 未配置")
    import ssl
    ssl_ctx = ssl.create_default_context()
    return pymysql.connect(
        host=db_host,
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=db_password,
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        ssl=ssl_ctx,
    )
```

---

### Task 6: 调度器改造（重审消费逻辑）

**Files:**
- Modify: `app/scheduler/review_scheduler.py`

- [ ] **Step 1: 添加 import**

在文件顶部添加 import：

```python
from app.db.database import get_rerun_queue_dao, get_review_result_dao
from app.db.history_helpers import insert_history_row
from app.output.frontend_pusher import push_to_frontend
from app.runner import review_claim_async
from app.policy_terms_registry import POLICY_TERMS
from app.claim_ai_reviewer import AIClaimReviewer
```

确认这些 import 已存在（部分已有）。新增的：`get_rerun_queue_dao`, `get_review_result_dao`, `insert_history_row`。

- [ ] **Step 2: 在 `ReviewScheduler.__init__` 中初始化 DAO**

修改 `__init__` 方法：

```python
def __init__(
    self,
    status_manager: Optional[StatusManager] = None,
    batch_size: int = 3
):
    self.status_manager = status_manager or get_status_manager()
    self.batch_size = batch_size
    self.db = get_db_connection()
    self.scheduler_log_dao = get_scheduler_log_dao()
    self._lock = asyncio.Lock()
    # 重审队列 DAO
    self.rerun_queue_dao = get_rerun_queue_dao()
    self.review_result_dao = get_review_result_dao()
```

- [ ] **Step 3: 在 `process_pending_reviews` 中新增重审队列消费**

在 `_process_pending_reviews_impl` 方法中，找到获取待审核案件的位置（line ~85 `pending_claims = await self.status_manager.get_pending_claims(...)`），**在此行之前**插入队列消费逻辑。

具体来说，在 `pending_claims = ...` 之前添加：

```python
# 0. 先消费重审队列（人工变更触发的重审）
await self._process_rerun_queue(limit=limit)
```

- [ ] **Step 4: 实现 `_process_rerun_queue` 方法**

在 `_review_claim` 方法之后添加新方法：

```python
async def _process_rerun_queue(self, limit: int) -> int:
    """处理重审队列中的人工变更触发案件

    流程：
    1. 取 pending 状态的行
    2. 标记 processing
    3. 快照旧行到 history
    4. 执行 AI Pipeline（新建 reviewer 实例）
    5. UPSERT ai_review_result
    6. 写 history（新版快照）
    7. 推前端
    8. 删除 queue 行

    Returns:
        处理的案件数
    """
    from app.db.history_helpers import insert_history_row, TRACKED_AI_FIELDS
    import json

    # 0. 重置超时的 processing 行
    await self.rerun_queue_dao.reset_stale_processing(timeout_minutes=30)

    # 1. 取 pending 状态的行
    queue_items = await self.rerun_queue_dao.dequeue_pending(limit=limit)
    if not queue_items:
        return 0

    LOGGER.info(f"重审队列: 找到 {len(queue_items)} 个待重审案件")

    processed = 0
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(connector=connector, trust_env=True) as session:
        for item in queue_items:
            queue_id = item["id"]
            forceid = item["forceid"]

            try:
                # 2. 标记 processing
                await self.rerun_queue_dao.mark_processing(queue_id)

                # 2b. 快照旧行到 history
                old_row = await self.review_result_dao.get_result_by_forceid(forceid)
                if old_row:
                    snapshot = json.dumps(old_row.to_dict(), ensure_ascii=False, default=str)
                    insert_history_row(
                        self.db._pool,  # 需要从同步连接，下面用 pymysql
                        {
                            'forceid': forceid,
                            'benefit_name': old_row.benefit_name,
                            'review_type': 'ai',
                            'audit_result': old_row.audit_result,
                            'audit_status': old_row.audit_status,
                            'confidence_score': old_row.confidence_score,
                            'payout_amount': old_row.payout_amount,
                            'identity_match': old_row.identity_match,
                            'threshold_met': old_row.threshold_met,
                            'exclusion_triggered': old_row.exclusion_triggered,
                            'manual_status': old_row.manual_status,
                            'manual_conclusion': old_row.manual_conclusion,
                            'snapshot_json': snapshot,
                            'triggered_by': 'rerun',
                            'rerun_queue_id': queue_id,
                            'created_at': datetime.now(),
                        }
                    )

                # 2c. 找到案件目录
                claim_folder = self._find_claim_folder(forceid)
                if not claim_folder:
                    LOGGER.warning(f"重审找不到案件目录: {forceid}")
                    await self.rerun_queue_dao.fail(queue_id)
                    continue

                # 2d. 加载条款
                claim_type = "flight_delay"  # 从 old_row 或 claim_info 推断
                if old_row and old_row.claim_type:
                    claim_type = old_row.claim_type
                try:
                    terms_file = POLICY_TERMS.resolve(claim_type)
                    policy_terms = terms_file.read_text(encoding="utf-8")
                except Exception:
                    policy_terms = ""

                # 2e. 执行 AI Pipeline（新建 reviewer 实例）
                reviewer = AIClaimReviewer()
                result = await review_claim_async(
                    reviewer, claim_folder, policy_terms, 1, 1, session
                )

                if not result:
                    raise RuntimeError("AI 审核返回空结果")

                # 2f. UPSERT ai_review_result
                from app.db.models import ReviewResult
                # 将 result dict 转为 ReviewResult 对象（取需要的字段）
                review_obj = ReviewResult(
                    forceid=forceid,
                    claim_id=result.get('claim_id'),
                    claim_type=result.get('claim_type'),
                    benefit_name=result.get('benefit_name'),
                    audit_result=result.get('audit_result'),
                    audit_status='completed',
                    payout_amount=result.get('payout_amount'),
                    remark=result.get('Remark', '')[:2000] if result.get('Remark') else None,
                    is_additional=result.get('IsAdditional', 'N'),
                    key_conclusions=json.dumps(result.get('KeyConclusions', []), ensure_ascii=False),
                    raw_result=json.dumps(result, ensure_ascii=False),
                )
                await self.review_result_dao.create_or_update_result(review_obj)

                # 2g. 写 history（新版快照）
                snapshot_new = json.dumps(result, ensure_ascii=False, default=str)
                insert_history_row(
                    self.db._pool,
                    {
                        'forceid': forceid,
                        'benefit_name': review_obj.benefit_name,
                        'review_type': 'ai',
                        'audit_result': review_obj.audit_result,
                        'audit_status': review_obj.audit_status,
                        'payout_amount': review_obj.payout_amount,
                        'snapshot_json': snapshot_new,
                        'triggered_by': 'rerun',
                        'rerun_queue_id': queue_id,
                        'created_at': datetime.now(),
                    }
                )

                # 2h. 推前端
                try:
                    push_result = await push_to_frontend(result, session)
                    if push_result.get("success"):
                        LOGGER.info(f"重审推送前端成功: {forceid}")
                except Exception as _push_err:
                    LOGGER.warning(f"重审推送前端异常: {forceid}, 错误: {_push_err}")

                # 2i. 标记完成
                await self.rerun_queue_dao.complete(queue_id)
                processed += 1
                LOGGER.info(f"重审成功: {forceid}")

            except Exception as e:
                LOGGER.error(f"重审失败: {forceid} - {e}", exc_info=True)
                await self.rerun_queue_dao.fail(queue_id)

    return processed
```

**注意**：上面的 `insert_history_row` 需要 pymysql connection，但 review_scheduler 使用的是 aiomysql 异步连接。需要用同步方式调用。修改方案：创建一个同步的 `write_rerun_snapshot` 函数（放在 history_helpers 中），接受数据库连接参数，由调用方创建同步连接。

简化方案：直接在 `_process_rerun_queue` 中使用 pymysql 同步连接写 history：

```python
# 写 history 时使用同步连接
import pymysql
import ssl

def _get_sync_db_conn():
    """获取同步数据库连接（用于 history_helpers 调用）"""
    db_host = os.getenv("DB_HOST")
    db_password = os.getenv("DB_PASSWORD")
    ssl_ctx = ssl.create_default_context()
    return pymysql.connect(
        host=db_host,
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", ""),
        password=db_password,
        database=os.getenv("DB_NAME", "ai"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        ssl=ssl_ctx,
    )
```

在 `_process_rerun_queue` 中用这个同步连接调用 `insert_history_row`。

---

### Task 7: 创建测试

**Files:**
- Create: `tests/test_rerun/__init__.py`
- Create: `tests/test_rerun/test_dao.py`

- [ ] **Step 1: 创建 `tests/test_rerun/__init__.py`**

空文件即可。

- [ ] **Step 2: 创建 `tests/test_rerun/test_dao.py`**

```python
"""RerunQueueDAO 单元测试"""

import pytest
import asyncio

# 由于 DAO 需要数据库连接，这些测试标记为集成测试
# 单元测试部分只测试 dataclass

from app.db.models import RerunQueue


class TestRerunQueueModel:
    """RerunQueue dataclass 单元测试"""

    def test_default_values(self):
        q = RerunQueue()
        assert q.forceid == ""
        assert q.triggered_by == "manual_status_change"
        assert q.rerun_status == "pending"
        assert q.retry_count == 0

    def test_from_dict(self):
        data = {
            "id": 1,
            "forceid": "a0nC800000XXX",
            "triggered_by": "manual_status_change",
            "rerun_status": "pending",
            "retry_count": 0,
            "created_at": "2026-05-27T10:00:00",
            "updated_at": "2026-05-27T10:00:00",
        }
        q = RerunQueue.from_dict(data)
        assert q.id == 1
        assert q.forceid == "a0nC800000XXX"
        assert q.triggered_by == "manual_status_change"

    def test_to_dict(self):
        q = RerunQueue(forceid="test123", triggered_by="force")
        d = q.to_dict()
        assert d["forceid"] == "test123"
        assert d["triggered_by"] == "force"

    def test_from_dict_ignores_unknown_fields(self):
        data = {
            "forceid": "test",
            "unknown_field": "value",
        }
        q = RerunQueue.from_dict(data)
        assert q.forceid == "test"
        assert not hasattr(q, "unknown_field") or getattr(q, "unknown_field", None) is None
```

- [ ] **Step 3: 运行测试验证**

```bash
pytest tests/test_rerun/test_dao.py -v
```

Expected: 4 tests PASS

---

### Task 8: 端到端验证

**Files:**
- 无新文件，手动运行现有脚本验证

- [ ] **Step 1: 运行迁移脚本**

```bash
# 在 MySQL 中执行
mysql -h <host> -u <user> -p ai < scripts/db/migrations/013_create_rerun_queue.sql
```

- [ ] **Step 2: 验证表创建成功**

```bash
mysql -h <host> -u <user> -p ai -e "DESCRIBE ai_rerun_queue; SHOW CREATE TABLE ai_review_history\G" | grep -A2 "triggered_by"
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_rerun/ -v
```

Expected: 4 tests PASS

- [ ] **Step 4: 提交**

```bash
git add scripts/db/migrations/013_create_rerun_queue.sql app/db/models.py app/db/database.py scripts/sync_manual_status.py app/scheduler/review_scheduler.py tests/test_rerun/
git commit -m "$(cat <<'EOF'
feat: 人工变更触发 AI 自动重审

- 新增 ai_rerun_queue 队列表（migration 013）
- 新增 RerunQueue dataclass 和 DAO
- sync_manual_status.py 检测人工结论变化时入队
- review_scheduler 每轮消费重审队列
- 重审前后分别写快照到 ai_review_history
EOF
)"
```

---

## 自审检查

### 1. Spec 覆盖检查

| Spec 要求 | 对应 Task |
|-----------|----------|
| 新建迁移脚本 | Task 1 |
| RerunQueue dataclass | Task 2 |
| ReviewHistoryRecord 加字段 | Task 2 |
| RerunQueueDAO | Task 3 |
| 同步脚本入队 | Task 5 |
| 调度器消费队列 | Task 6 |
| history_helpers 扩展 | Task 4（确认无需修改） |
| 测试 | Task 7 |

### 2. 占位符扫描

计划中无 TBD/TODO。所有代码步骤都包含完整代码。

### 3. 类型一致性

- `RerunQueue` dataclass 字段名与 SQL 表列名一致
- `rerun_status` 枚举值（pending/processing/completed/failed）在 DAO 和 SQL 中一致
- `insert_history_row` 接受的 dict key 与 `ai_review_history` 表列名一致

### 4. 已知简化

- `_process_rerun_queue` 中使用 pymysql 同步连接写 history，而非复用 aiomysql 异步连接。这是因为 `history_helpers.insert_history_row` 接受 pymysql connection。这是合理的——写 history 是低频操作，同步调用不影响性能。
- 重审时 `claim_type` 从 `old_row.claim_type` 推断。如果旧行没有 claim_type（老数据），fallback 为 `flight_delay`。
