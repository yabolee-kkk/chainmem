"""Tests for structured cross-agent work events."""

from __future__ import annotations

import sqlite3

import pytest

from chainmem.store.sqlite_store import SQLiteStore
from chainmem.work_memory import normalize_work_event, redact_secrets


def test_redact_secrets_masks_common_tokens():
    text, redacted = redact_secrets(
        "token sk-abcdefghijklmnopqrstuvwxyz123456 and password=abcdefgh"
    )
    assert redacted is True
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert "abcdefgh" not in text
    assert "[REDACTED]" in text


def test_normalize_work_event_validates_and_hashes():
    event = normalize_work_event(
        agent_id="hermes",
        task_summary="完成 ChainMem 部署",
        host="borong",
        session_id="session-1",
        project="chainmem",
        status="done",
        artifacts=["docs/work-memory.md"],
        tags=["部署", "记忆"],
        source="feishu",
        occurred_at="2026-10-20T13:20:00Z",
    )
    assert event["status"] == "done"
    assert len(event["content_hash"]) == 64
    assert event["occurred_at"] == "2026-10-20T13:20:00Z"
    assert event["artifacts"] == ["docs/work-memory.md"]


def test_normalize_work_event_rejects_empty_summary():
    with pytest.raises(ValueError, match="task_summary"):
        normalize_work_event(agent_id="hermes", task_summary="   ")


def test_work_events_table_migrates_existing_database(tmp_path):
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE chains (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    store.initialize()
    columns = {
        row["name"]
        for row in store.conn.execute("PRAGMA table_info(work_events)").fetchall()
    }
    assert {
        "id",
        "agent_id",
        "project",
        "task_summary",
        "occurred_at",
        "content_hash",
        "visibility",
    } <= columns
    store.close()


def test_save_and_get_work_event(tmp_path):
    store = SQLiteStore(str(tmp_path / "data.db"))
    store.initialize()
    event = normalize_work_event(
        agent_id="codex",
        task_summary="实现工作事件表",
        project="chainmem",
        status="active",
        occurred_at="2026-10-22T02:00:00Z",
    )
    saved = store.save_work_event(event)
    loaded = store.get_work_event(saved["id"])
    assert loaded["agent_id"] == "codex"
    assert loaded["artifacts"] == []
    assert loaded["visibility"] == "shared"
    store.close()


def test_list_work_events_filters_days_agent_project_status(tmp_path):
    store = SQLiteStore(str(tmp_path / "data.db"))
    store.initialize()
    store.save_work_event(
        normalize_work_event(
            agent_id="hermes",
            task_summary="ChainMem 部署",
            project="chainmem",
            status="done",
            occurred_at="2026-10-22T01:00:00Z",
        )
    )
    store.save_work_event(
        normalize_work_event(
            agent_id="codex",
            task_summary="博客修复",
            project="blog",
            status="active",
            occurred_at="2026-09-01T01:00:00Z",
        )
    )
    results = store.list_work_events(
        since="2026-10-20T00:00:00Z",
        agent_id="hermes",
        project="chainmem",
        status="done",
    )
    assert [item["task_summary"] for item in results] == ["ChainMem 部署"]
    store.close()
