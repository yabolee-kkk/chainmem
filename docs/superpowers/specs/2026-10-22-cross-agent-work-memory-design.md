# Cross-Agent Work Memory Design

**Date:** 2026-10-22

**Status:** Proposed for implementation review

**Scope:** First production-capable version of a shared work memory for AI
agents connected to the ChainMem service.

## Goal

Allow any authorized AI agent on the local network to answer questions such as:

> Which agent worked on the ChainMem integration two days ago, and what was
> produced?

The service records compact work events in ChainMem, links them to an optional
conversation chain, and provides semantic recall plus structured timeline
queries.

## Non-Goals

- ChainMem does not replace the canonical knowledge base.
- This version does not index the entire knowledge base.
- This version does not automatically write conclusions into the knowledge base.
- This version does not store every token or every message by default.
- This version does not expose an unauthenticated MCP endpoint to the LAN or
  internet.

## Architecture

```text
AI agent
  |
  | MCP over HTTP or trusted local Unix socket
  v
ChainMem work memory service
  |
  +-- work_events table: structured work attribution
  +-- chains/nodes: optional raw conversation and semantic retrieval
  |
  v
Existing knowledge base remains the source of durable, verified conclusions.
```

Each work event stores a concise task summary. When raw conversation is
available, it is stored as a ChainMem chain and linked through `raw_chain_id`.
Structured fields answer who, when, where, and status. Semantic retrieval
answers what the user remembers about the work.

## Canonicality

- Raw episode and attribution: ChainMem.
- Stable user and agent preferences: `MEMORY.md`.
- Verified project facts and knowledge: `/data/knowledge_base`.
- Current task context: the active agent session.

ChainMem results are evidence for recall, not authoritative project truth.
Agents must preserve this distinction when reporting remembered work.

## Data Model

Add a `work_events` table through an idempotent SQLite migration.

```sql
CREATE TABLE IF NOT EXISTS work_events (
    id             TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    host           TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    project        TEXT NOT NULL DEFAULT '',
    task_summary   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'unknown',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    tags_json      TEXT NOT NULL DEFAULT '[]',
    raw_chain_id   TEXT DEFAULT NULL,
    source         TEXT NOT NULL DEFAULT '',
    started_at     TEXT DEFAULT NULL,
    ended_at       TEXT DEFAULT NULL,
    occurred_at    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    visibility     TEXT NOT NULL DEFAULT 'shared'
);
```

Required indexes:

- `(occurred_at DESC)`
- `(agent_id, occurred_at DESC)`
- `(project, occurred_at DESC)`
- `(session_id)`
- `(raw_chain_id)`
- `UNIQUE(agent_id, session_id, content_hash)`

`status` accepts `planned`, `active`, `blocked`, `done`, `failed`, and
`unknown`. Unknown values become `unknown`.

`visibility` accepts `shared` and `private`. The default is `shared`.
Private events are visible only to the owning agent. A trusted local operator
may request private events explicitly by passing the owning agent ID.

All stored timestamps use UTC ISO 8601 strings. Agents are responsible for
converting relative phrases such as "two days ago" into an explicit day range.

## Structured API

Add a focused module at `src/chainmem/work_memory.py` containing validation,
redaction, serialization, and retrieval orchestration.

```python
@dataclass(frozen=True)
class WorkEvent:
    id: str
    agent_id: str
    host: str
    session_id: str
    project: str
    task_summary: str
    status: str
    artifacts: list[str]
    tags: list[str]
    raw_chain_id: str | None
    source: str
    started_at: str | None
    ended_at: str | None
    occurred_at: str
    created_at: str
    visibility: str
```

Public `ChainMemory` methods:

```python
def record_work_event(
    self,
    *,
    agent_id: str,
    task_summary: str,
    host: str = "",
    session_id: str = "",
    project: str = "",
    status: str = "unknown",
    artifacts: list[str] | None = None,
    tags: list[str] | None = None,
    source: str = "",
    occurred_at: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    raw_text: str | None = None,
    visibility: str = "shared",
) -> WorkEvent:
    ...

def recall_work_events(
    self,
    *,
    query: str,
    days: int = 7,
    agent_id: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = 5,
    include_private_for: str | None = None,
) -> list[dict]:
    ...

def list_work_events(
    self,
    *,
    days: int = 7,
    agent_id: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = 30,
    include_private_for: str | None = None,
) -> list[dict]:
    ...

def get_work_event(
    self,
    event_id: str,
    *,
    include_private_for: str | None = None,
) -> dict | None:
    ...
```

Validation limits:

- `agent_id`: required, 1-64 characters.
- `project`: 0-128 characters.
- `task_summary`: required, 1-2000 characters.
- `session_id`: 0-128 characters.
- `artifacts`: at most 50 entries, each at most 500 characters.
- `tags`: at most 30 entries, each at most 64 characters.
- `limit`: 1-50.
- `days`: 1-365.

## Semantic Recall

Work event recording always creates a searchable chain:

- If `raw_text` is provided, the raw text is ingested and linked.
- Otherwise, the task summary and structured context are ingested.
- Chain source is `work-memory:<event-id>`.
- Chain tags include `work-memory`, `agent:<agent-id>`, project tag when
  present, and status tag.

`recall_work_events` first applies structured SQL filters for time range,
agent, project, status, and visibility. It then ranks matching events through
ChainMem semantic retrieval. If the embedding model or semantic path is
unavailable, it falls back to SQLite FTS5 or case-insensitive substring
matching.

Return at most `limit` results. Each result includes:

```json
{
  "event_id": "...",
  "agent_id": "hermes",
  "host": "borong",
  "session_id": "...",
  "project": "chainmem",
  "task_summary": "...",
  "status": "done",
  "artifacts": ["docs/work-memory.md"],
  "tags": ["chainmem", "deployment"],
  "occurred_at": "2026-10-20T13:20:00Z",
  "raw_chain_id": "...",
  "score": 0.87
}
```

The default maximum combined returned text is 4000 characters.

## MCP Tools

Add four tools without changing the semantics of the existing three tools:

### `work_memory_record`

Inputs:

- `task_summary` required
- `project`, `session_id`, `status`, `source`, `host`
- `artifacts`, `tags` as string arrays
- `occurred_at`, `started_at`, `ended_at`
- `raw_text` optional
- `visibility`

The authenticated agent ID is authoritative when HTTP agent tokens are enabled.
A caller-provided `agent_id` is ignored in that mode.

### `work_memory_recall`

Inputs:

- `query` required
- `days`, `limit`
- optional `agent_id`, `project`, `status`

Output contains a readable list of candidate events and exact timestamps.
The response must state that these are remembered work events, not verified
knowledge-base facts.

### `work_memory_timeline`

Inputs:

- `days`, `limit`
- optional `agent_id`, `project`, `status`

Output is ordered newest first and includes agent identity, task summary,
status, project, and artifacts.

### `work_memory_get`

Input:

- `event_id`

Output includes the full structured event and linked raw chain ID.

## HTTP Authentication and Identity

Add `--http-host` with default `127.0.0.1`.

Add `--agent-tokens-file PATH`. The file format is one entry per line:

```text
agent-id:token
```

Empty lines and lines beginning with `#` are ignored. The file must have mode
`600` or stricter.

Rules:

- A loopback-only HTTP service may run without agent tokens for backward
  compatibility.
- A non-loopback HTTP bind requires a valid agent tokens file. Startup fails
  otherwise.
- When tokens are enabled, `/mcp` requires
  `Authorization: Bearer <token>`.
- The server maps the token to `agent_id` using constant-time comparisons.
- Tokens are never logged or included in MCP responses.
- Existing read/write tools remain available, but work-memory writes always
  use the authenticated identity.
- The Unix socket remains local-only and trusted. Its work-memory record tool
  requires an explicit `agent_id`.

## Redaction

Apply redaction to summaries, project fields, tags, artifacts, and source
before storage. Reuse and extend the credential patterns in
`chainmem.core.crypto`.

Redact:

- GitHub, GitLab, PyPI, OpenAI, DeepSeek, Anthropic, AWS, Google, Slack, and
  generic bearer tokens.
- Password, secret, and API-key assignments.
- PEM private key blocks.

The redactor replaces matched values with `[REDACTED]` and reports only a
boolean `redacted` flag to the caller.

## CLI

Add:

```text
chainmem work record ...
chainmem work recall QUERY --days 7 ...
chainmem work timeline --days 7 ...
chainmem work get EVENT_ID ...
```

CLI commands use the same validation and storage paths as MCP. The CLI
requires an explicit `--agent-id` because it is a local operator interface.

## Error Handling

- Invalid input returns a validation error with the field name.
- Unauthorized HTTP requests return HTTP 401.
- Events outside the caller's visibility return not found.
- Missing event IDs return not found.
- Semantic model errors fall back to structured text search and emit no token
  or secret data in logs.
- Duplicate work events are ignored through the unique constraint and return
  the existing event.

## Security and Operations

- The default bind remains loopback-only.
- LAN exposure is opt-in and requires agent tokens.
- Token files must not be committed.
- Back up `~/.chainmem/data.db` before the first production migration.
- Work memory stores summaries by default; raw conversation storage is opt-in.
- Do not store credentials, full environment dumps, or private keys.
- Add a documented rollback using `git revert` and restoration of the database
  backup.

## Testing

Unit tests:

- schema migration is idempotent and preserves existing chains/nodes.
- record validation and status normalization.
- secret redaction.
- duplicate event handling.
- project, agent, status, visibility, and date-range filters.
- timeline ordering.
- semantic recall with a linked chain.
- fallback recall when semantic search is unavailable.

Integration tests:

- agent A records work; agent B recalls the work and sees agent A.
- a caller cannot spoof agent identity when HTTP tokens are enabled.
- unauthorized LAN-mode startup fails without a token file.
- HTTP and Unix socket MCP tool lists contain all seven tools.
- existing ChainMem tests continue to pass.

Acceptance criteria:

```text
Given agent hermes records a ChainMem deployment task two days ago,
when any authorized agent asks what happened,
then recall returns hermes, the task summary, timestamp, project, and artifacts.

Given an HTTP request supplies another agent_id,
when token authentication is enabled,
then the stored agent_id remains the identity mapped from the token.

Given no matching event exists,
then the service returns an empty result instead of fabricating one.
```

## Rollout

1. Commit this design document.
2. Implement storage and core APIs.
3. Implement MCP tools and authentication.
4. Add CLI and documentation.
5. Run the full test suite and a two-agent integration test.
6. Merge to `main` only after verification.
7. Back up the production database.
8. Deploy with loopback-only mode first.
9. Configure agent tokens and switch to a LAN-accessible bind.
10. Restart the service and verify all MCP tools over HTTP and Unix socket.

## Rollback

- Code rollback: `git revert <commit>` on `main`, push, restart service.
- Deployment rollback: switch the service unit back to the previous commit and
  restart.
- Data rollback: stop the service and restore the pre-migration database
  backup. The new table is additive, but restoring the backup guarantees a
  clean state.
