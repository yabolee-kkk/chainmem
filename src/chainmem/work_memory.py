"""Structured work-event memory shared by multiple AI agents."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from chainmem.core.crypto import CREDENTIAL_PATTERNS


VALID_STATUSES = {"planned", "active", "blocked", "done", "failed", "unknown"}
VALID_VISIBILITIES = {"shared", "private"}

_EXTRA_REDACTION_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key)\s*[=:]\s*\S+"),
]


def redact_secrets(text: str) -> tuple[str, bool]:
    """Mask credentials before writing work-memory fields."""
    if not text:
        return text, False

    cleaned = text
    for pattern in [*CREDENTIAL_PATTERNS, *_EXTRA_REDACTION_PATTERNS]:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned, cleaned != text


def _clean_text(
    value: str | None,
    *,
    field: str,
    max_length: int,
    required: bool = False,
) -> str:
    text = (value or "").strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    cleaned, _ = redact_secrets(text)
    return cleaned


def _clean_list(
    values: Iterable[str] | str | None,
    *,
    field: str,
    max_items: int,
    max_item_length: int,
) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        candidates = [part.strip() for part in values.split(",")]
    else:
        candidates = [str(part).strip() for part in values]

    result: list[str] = []
    for item in candidates:
        if not item:
            continue
        if len(item) > max_item_length:
            raise ValueError(
                f"{field} item exceeds {max_item_length} characters"
            )
        cleaned, _ = redact_secrets(item)
        if cleaned not in result:
            result.append(cleaned)
    if len(result) > max_items:
        raise ValueError(f"{field} exceeds {max_items} items")
    return result


def _parse_datetime(
    value: str | None,
    *,
    field: str,
    default_now: bool = False,
) -> str | None:
    if not value:
        if not default_now:
            return None
        value = datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def normalize_work_event(
    *,
    agent_id: str,
    task_summary: str,
    host: str = "",
    session_id: str = "",
    project: str = "",
    status: str = "unknown",
    artifacts: Iterable[str] | str | None = None,
    tags: Iterable[str] | str | None = None,
    source: str = "",
    occurred_at: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    visibility: str = "shared",
) -> dict[str, Any]:
    agent = _clean_text(agent_id, field="agent_id", max_length=64, required=True)
    summary = _clean_text(
        task_summary,
        field="task_summary",
        max_length=2000,
        required=True,
    )
    normalized_status = (status or "unknown").strip().lower()
    if normalized_status not in VALID_STATUSES:
        normalized_status = "unknown"
    normalized_visibility = (visibility or "shared").strip().lower()
    if normalized_visibility not in VALID_VISIBILITIES:
        raise ValueError("visibility must be 'shared' or 'private'")

    normalized_occurred = _parse_datetime(
        occurred_at,
        field="occurred_at",
        default_now=True,
    )
    assert normalized_occurred is not None
    normalized_started = _parse_datetime(started_at, field="started_at")
    normalized_ended = _parse_datetime(ended_at, field="ended_at")

    normalized_artifacts = _clean_list(
        artifacts,
        field="artifacts",
        max_items=50,
        max_item_length=500,
    )
    normalized_tags = _clean_list(
        tags,
        field="tags",
        max_items=30,
        max_item_length=64,
    )
    normalized_session = _clean_text(
        session_id,
        field="session_id",
        max_length=128,
    )
    normalized_project = _clean_text(
        project,
        field="project",
        max_length=128,
    )
    normalized_host = _clean_text(host, field="host", max_length=255)
    normalized_source = _clean_text(source, field="source", max_length=255)

    hash_input = json.dumps(
        {
            "agent_id": agent,
            "session_id": normalized_session,
            "task_summary": summary,
            "artifacts": normalized_artifacts,
            "occurred_at": normalized_occurred,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    content_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
    created_at = datetime.now(timezone.utc).replace(microsecond=0)

    return {
        "id": str(uuid.uuid4()),
        "agent_id": agent,
        "host": normalized_host,
        "session_id": normalized_session,
        "project": normalized_project,
        "task_summary": summary,
        "status": normalized_status,
        "artifacts": normalized_artifacts,
        "tags": normalized_tags,
        "raw_chain_id": None,
        "source": normalized_source,
        "started_at": normalized_started,
        "ended_at": normalized_ended,
        "occurred_at": normalized_occurred,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "content_hash": content_hash,
        "visibility": normalized_visibility,
    }


def parse_work_event(row: dict[str, Any] | Any) -> dict[str, Any]:
    """Convert a SQLite row to a public work-event dictionary."""
    event = dict(row)
    for source_key, target_key in (
        ("artifacts_json", "artifacts"),
        ("tags_json", "tags"),
    ):
        raw = event.pop(source_key, "[]")
        event[target_key] = json.loads(raw or "[]")
    return event


def format_work_event(event: dict[str, Any], *, include_score: bool = False) -> str:
    """Render a compact, readable work event for MCP responses."""
    lines = [
        f"事件 ID: {event['id']}",
        f"Agent: {event['agent_id']}",
        f"时间: {event['occurred_at']}",
    ]
    if event.get("project"):
        lines.append(f"项目: {event['project']}")
    lines.append(f"状态: {event.get('status', 'unknown')}")
    lines.append(f"任务: {event['task_summary']}")
    if event.get("artifacts"):
        lines.append(f"产出: {', '.join(event['artifacts'])}")
    if event.get("raw_chain_id"):
        lines.append(f"记忆链: {event['raw_chain_id']}")
    if include_score and "score" in event:
        lines.append(f"匹配分数: {event['score']:.3f}")
    return "\n".join(lines)
