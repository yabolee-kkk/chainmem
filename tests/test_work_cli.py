"""Tests for the work-memory CLI."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from chainmem.cli.app import app


runner = CliRunner()


def test_work_cli_record_recall_timeline_and_get(tmp_path):
    db_path = str(tmp_path / "data.db")
    record = runner.invoke(
        app,
        [
            "work",
            "record",
            "完成 ChainMem 跨 Agent 工作记忆",
            "--agent-id",
            "hermes",
            "--project",
            "chainmem",
            "--status",
            "done",
            "--artifacts",
            "docs/work-memory.md",
            "--tags",
            "部署,记忆",
            "--db",
            db_path,
        ],
    )
    assert record.exit_code == 0, record.output
    match = re.search(r"事件 ID:\s*([0-9a-f-]{36})", record.output)
    assert match
    event_id = match.group(1)

    timeline = runner.invoke(
        app,
        [
            "work",
            "timeline",
            "--days",
            "7",
            "--project",
            "chainmem",
            "--db",
            db_path,
        ],
    )
    assert timeline.exit_code == 0, timeline.output
    assert "hermes" in timeline.output
    assert "完成 ChainMem 跨 Agent 工作记忆" in timeline.output

    recall = runner.invoke(
        app,
        [
            "work",
            "recall",
            "局域网记忆服务",
            "--days",
            "7",
            "--db",
            db_path,
        ],
    )
    assert recall.exit_code == 0, recall.output
    assert "hermes" in recall.output

    detail = runner.invoke(
        app,
        [
            "work",
            "get",
            event_id,
            "--agent-id",
            "hermes",
            "--db",
            db_path,
        ],
    )
    assert detail.exit_code == 0, detail.output
    assert event_id in detail.output
    assert "docs/work-memory.md" in detail.output


def test_work_record_requires_agent_id(tmp_path):
    result = runner.invoke(
        app,
        [
            "work",
            "record",
            "缺少身份",
            "--db",
            str(tmp_path / "data.db"),
        ],
    )
    assert result.exit_code != 0
    assert "agent-id" in result.output
