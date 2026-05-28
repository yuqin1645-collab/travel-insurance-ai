"""RerunQueue dataclass 单元测试"""

from app.db.models import RerunQueue


class TestRerunQueueModel:
    """RerunQueue dataclass 单元测试"""

    def test_default_values(self):
        q = RerunQueue()
        assert q.forceid == ""
        assert q.triggered_by == "manual_status_change"
        assert q.rerun_status == "pending"
        assert q.retry_count == 0
        assert q.created_at is None
        assert q.updated_at is None

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

    def test_from_dict_parses_datetime_strings(self):
        data = {
            "forceid": "abc123",
            "created_at": "2026-05-27T10:00:00",
            "updated_at": "2026-05-27T11:30:00",
        }
        q = RerunQueue.from_dict(data)
        from datetime import datetime
        assert isinstance(q.created_at, datetime)
        assert isinstance(q.updated_at, datetime)
        assert q.created_at.hour == 10
        assert q.updated_at.hour == 11

    def test_to_dict_serializes_datetime(self):
        from datetime import datetime
        dt = datetime(2026, 5, 27, 10, 0, 0)
        q = RerunQueue(forceid="x", created_at=dt)
        d = q.to_dict()
        assert d["created_at"] == "2026-05-27T10:00:00"
