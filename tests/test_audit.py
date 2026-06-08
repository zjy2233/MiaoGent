"""Tests for AuditLogger."""

import os
import tempfile
import time

import pytest

from src.store.audit import AuditLogger, AuditRecord, MAX_RECORDS


@pytest.fixture
def audit_logger():
    """Create a temporary AuditLogger for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()
    logger = AuditLogger(tmp_path)
    yield logger
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


class TestAuditLogger:
    """AuditLogger 功能测试。"""

    def test_log_and_query(self, audit_logger):
        audit_logger.log_simple("echo hello", 0, 0.1)
        records = audit_logger.query(limit=10)
        assert len(records) >= 1
        assert records[0]["command"] == "echo hello"
        assert records[0]["returncode"] == 0

    def test_log_audit_record(self, audit_logger):
        record = AuditRecord(
            timestamp=time.time(),
            command="ls -la",
            returncode=0,
            duration=0.05,
            stdout_size=1024,
            session_id="test-session",
            approved=True,
        )
        audit_logger.log(record)
        records = audit_logger.query(limit=10)
        assert len(records) >= 1
        assert records[0]["command"] == "ls -la"
        assert records[0]["stdout_size"] == 1024

    def test_count(self, audit_logger):
        assert audit_logger.count() == 0
        audit_logger.log_simple("cmd1", 0, 0.1)
        audit_logger.log_simple("cmd2", 1, 0.2)
        assert audit_logger.count() == 2

    def test_multiple_logs(self, audit_logger):
        for i in range(10):
            audit_logger.log_simple(f"cmd{i}", i % 2, 0.1)
        records = audit_logger.query(limit=5)
        assert len(records) == 5
        assert records[0]["command"] == "cmd9"  # 最新在前

    def test_clear(self, audit_logger):
        audit_logger.log_simple("test", 0, 0.1)
        assert audit_logger.count() > 0
        audit_logger.clear()
        assert audit_logger.count() == 0

    def test_audit_record_to_dict(self):
        record = AuditRecord(
            timestamp=1234567890.0,
            command="test",
            returncode=0,
            duration=0.1,
        )
        d = record.to_dict()
        assert d["command"] == "test"
        assert d["timestamp"] == 1234567890.0

    def test_session_id(self, audit_logger):
        audit_logger.log_simple("cmd1", 0, 0.1, session_id="session-A")
        audit_logger.log_simple("cmd2", 0, 0.2, session_id="session-B")
        records = audit_logger.query(limit=10)
        session_ids = {r["session_id"] for r in records}
        assert "session-A" in session_ids
        assert "session-B" in session_ids

    def test_log_approved_flag(self, audit_logger):
        audit_logger.log_simple("cmd1", 0, 0.1, approved=True)
        audit_logger.log_simple("cmd2", 0, 0.2, approved=False)
        records = audit_logger.query(limit=10)
        approveds = [r["approved"] for r in records]
        assert 1 in approveds  # True = 1
        assert 0 in approveds  # False = 0


class TestAuditLoggerRotation:
    """测试 MAX_RECORDS 自动清理。"""

    def test_cleanup_old_records(self):
        """超过 MAX_RECORDS 时自动清理最老的记录。"""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            logger = AuditLogger(tmp_path)
            # 插入 MAX_RECORDS + 50 条
            n = MAX_RECORDS + 50
            for i in range(n):
                logger.log_simple(f"cmd{i}", i % 2, 0.01)

            count = logger.count()
            assert count <= MAX_RECORDS + 10  # 不超过阈值太多

            # 验证：最新的记录应该存在，老的可能被删
            records = logger.query(limit=5)
            assert len(records) == 5
            # 最新记录是 cmd{n-1}
            assert records[0]["command"] == f"cmd{n - 1}"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
