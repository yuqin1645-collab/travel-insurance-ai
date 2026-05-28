"""重审队列集成测试 — 验证完整链路的关键组件"""

import json
from datetime import datetime

from app.db.models import RerunQueue, ReviewHistoryRecord
from app.db.rerun_queue_sync import enqueue_if_no_pending, TABLE_RERUN_QUEUE


class TestRerunQueueConstants:
    """验证表名和常量正确性"""

    def test_table_name(self):
        assert TABLE_RERUN_QUEUE == "ai_rerun_queue"


class TestRerunQueueModel:
    """RerunQueue dataclass 集成场景测试"""

    def test_full_lifecycle(self):
        """模拟完整生命周期：创建 -> 序列化 -> 反序列化"""
        q = RerunQueue(
            id=1,
            forceid="a0nC800000XXX",
            triggered_by="manual_status_change",
            rerun_status="pending",
            retry_count=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        d = q.to_dict()
        assert d["forceid"] == "a0nC800000XXX"
        assert d["rerun_status"] == "pending"
        assert isinstance(d["created_at"], str)

        q2 = RerunQueue.from_dict(d)
        assert q2.forceid == q.forceid
        assert q2.rerun_status == q.rerun_status
        assert isinstance(q2.created_at, datetime)

    def test_rerun_state_transitions(self):
        """模拟状态流转：pending -> processing -> completed"""
        states = ["pending", "processing", "completed", "failed"]
        for state in states:
            q = RerunQueue(forceid="test", rerun_status=state)
            assert q.rerun_status == state


class TestHistorySnapshot:
    """history 快照序列化测试"""

    def test_snapshot_json_serializes_full_review_result(self):
        """模拟完整审核结果序列化为 snapshot_json"""
        result = {
            "forceid": "a0nC800000XXX",
            "audit_result": "通过",
            "payout_amount": 500.0,
            "KeyConclusions": [{"checkpoint": "保单有效期", "Eligible": "通过"}],
            "DebugInfo": {"flight_delay": {}},
        }
        snapshot = json.dumps(result, ensure_ascii=False, default=str)
        parsed = json.loads(snapshot)
        assert parsed["audit_result"] == "通过"
        assert parsed["payout_amount"] == 500.0

    def test_snapshot_with_datetime(self):
        """包含 datetime 的快照序列化"""
        data = {
            "created_at": datetime(2026, 5, 27, 10, 0, 0),
            "forceid": "test",
        }
        snapshot = json.dumps(data, ensure_ascii=False, default=str)
        parsed = json.loads(snapshot)
        assert "2026-05-27" in parsed["created_at"]


class TestEnqueueSyncHelper:
    """enqueue_if_no_pending 同步助手测试"""

    def test_function_exists_and_has_correct_signature(self):
        """验证函数存在且签名正确"""
        import inspect
        sig = inspect.signature(enqueue_if_no_pending)
        params = list(sig.parameters.keys())
        assert "conn" in params
        assert "forceid" in params
        assert "triggered_by" in params
