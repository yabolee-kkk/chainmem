"""SQLite 存储层——节点和链的持久化"""

from __future__ import annotations
import json
import sqlite3
from typing import Optional
import numpy as np

from chainmem.work_memory import parse_work_event


class SQLiteStore:
    """SQLite 存储，管理 nodes 和 chains 两张表"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def initialize(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chains (
                id            TEXT PRIMARY KEY,
                anchor_prefix TEXT NOT NULL,
                root_id       TEXT NOT NULL,
                leaf_id       TEXT NOT NULL,
                node_count    INTEGER NOT NULL DEFAULT 0,
                summary       TEXT DEFAULT '',
                source        TEXT DEFAULT '',
                tags          TEXT DEFAULT '[]',
                strength      REAL DEFAULT 1.0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_access   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS nodes (
                id          TEXT PRIMARY KEY,
                chain_id    TEXT NOT NULL,
                seq         INTEGER NOT NULL,
                text        TEXT NOT NULL,
                text_prefix TEXT NOT NULL,
                prev_id     TEXT,
                next_id     TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chain_id) REFERENCES chains(id)
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_chain ON nodes(chain_id, seq);
            CREATE INDEX IF NOT EXISTS idx_nodes_next ON nodes(next_id);

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
                visibility     TEXT NOT NULL DEFAULT 'shared',
                UNIQUE(agent_id, session_id, content_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_work_events_occurred
                ON work_events(occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_work_events_agent
                ON work_events(agent_id, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_work_events_project
                ON work_events(project, occurred_at DESC);
            CREATE INDEX IF NOT EXISTS idx_work_events_session
                ON work_events(session_id);
            CREATE INDEX IF NOT EXISTS idx_work_events_chain
                ON work_events(raw_chain_id);
        """)
        self.conn.commit()

        # ── 数据库迁移：添加加密列（老数据库升级） ──
        self._migrate_add_column("nodes", "encrypted", "INTEGER NOT NULL DEFAULT 0")
        self._migrate_add_column("nodes", "encryption_iv", "TEXT DEFAULT ''")

    def _migrate_add_column(self, table: str, column: str, col_def: str):
        """如果表中不存在指定列，则 ALTER TABLE ADD COLUMN"""
        cursor = self.conn.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in cursor.fetchall()}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            self.conn.commit()

    # ── Chain CRUD ──

    def save_chain(self, chain_id: str, anchor_prefix: str, root_id: str,
                   leaf_id: str, node_count: int, source: str = "",
                   tags: list[str] | None = None, strength: float = 1.0):
        self.conn.execute(
            """INSERT OR REPLACE INTO chains
               (id, anchor_prefix, root_id, leaf_id, node_count, source, tags, strength)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chain_id, anchor_prefix, root_id, leaf_id, node_count,
             source, json.dumps(tags or []), strength),
        )
        self.conn.commit()

    def update_chain_access(self, chain_id: str):
        self.conn.execute(
            "UPDATE chains SET last_access = CURRENT_TIMESTAMP WHERE id = ?",
            (chain_id,),
        )
        self.conn.commit()

    def get_chain(self, chain_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM chains WHERE id = ?", (chain_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_all_chains(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, anchor_prefix, node_count, source, tags, strength, created_at, last_access FROM chains ORDER BY created_at"
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("tags"), str):
                d["tags"] = json.loads(d["tags"])
            results.append(d)
        return results

    # ── Node CRUD ──

    def save_node(self, node_id: str, chain_id: str, seq: int, text: str,
                  prev_id: str | None = None, next_id: str | None = None,
                  encrypted: bool = False, encryption_iv: str = ""):
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, chain_id, seq, text, text_prefix, prev_id, next_id, encrypted, encryption_iv)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (node_id, chain_id, seq, text, text[:3],
             prev_id, next_id, 1 if encrypted else 0, encryption_iv),
        )
        self.conn.commit()

    def get_node(self, node_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_nodes_by_chain(self, chain_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE chain_id = ? ORDER BY seq",
            (chain_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_nodes_with_embeddings_dense(self) -> list[dict]:
        """返回所有节点（不含 embedding，由上层加载）"""
        rows = self.conn.execute(
            "SELECT id, chain_id, seq, text, prev_id, next_id, encrypted, encryption_iv FROM nodes ORDER BY seq"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Work event CRUD ──

    def save_work_event(self, event: dict) -> dict:
        self.conn.execute(
            """INSERT OR IGNORE INTO work_events
               (id, agent_id, host, session_id, project, task_summary, status,
                artifacts_json, tags_json, raw_chain_id, source, started_at,
                ended_at, occurred_at, created_at, content_hash, visibility)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event["id"],
                event["agent_id"],
                event.get("host", ""),
                event.get("session_id", ""),
                event.get("project", ""),
                event["task_summary"],
                event.get("status", "unknown"),
                json.dumps(event.get("artifacts", []), ensure_ascii=False),
                json.dumps(event.get("tags", []), ensure_ascii=False),
                event.get("raw_chain_id"),
                event.get("source", ""),
                event.get("started_at"),
                event.get("ended_at"),
                event["occurred_at"],
                event["created_at"],
                event["content_hash"],
                event.get("visibility", "shared"),
            ),
        )
        self.conn.commit()
        saved = self.get_work_event(event["id"])
        if saved is not None:
            return saved

        existing = self.conn.execute(
            """SELECT * FROM work_events
               WHERE agent_id=? AND session_id=? AND content_hash=?""",
            (
                event["agent_id"],
                event.get("session_id", ""),
                event["content_hash"],
            ),
        ).fetchone()
        if existing is None:
            raise RuntimeError("work event was not saved")
        return parse_work_event(existing)

    def update_work_event_chain(self, event_id: str, chain_id: str) -> None:
        self.conn.execute(
            "UPDATE work_events SET raw_chain_id=? WHERE id=?",
            (chain_id, event_id),
        )
        self.conn.commit()

    def get_work_event(self, event_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM work_events WHERE id=?",
            (event_id,),
        ).fetchone()
        return parse_work_event(row) if row is not None else None

    def find_work_event(
        self,
        agent_id: str,
        session_id: str,
        content_hash: str,
    ) -> dict | None:
        row = self.conn.execute(
            """SELECT * FROM work_events
               WHERE agent_id=? AND session_id=? AND content_hash=?""",
            (agent_id, session_id, content_hash),
        ).fetchone()
        return parse_work_event(row) if row is not None else None

    def list_work_events(
        self,
        *,
        since: str | None = None,
        agent_id: str | None = None,
        project: str | None = None,
        status: str | None = None,
        limit: int = 30,
        include_private_for: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[object] = []

        if include_private_for:
            clauses.append("(visibility='shared' OR agent_id=?)")
            params.append(include_private_for)
        else:
            clauses.append("visibility='shared'")
        if since:
            clauses.append("occurred_at>=?")
            params.append(since)
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if project:
            clauses.append("project=?")
            params.append(project)
        if status:
            clauses.append("status=?")
            params.append(status)

        sql = "SELECT * FROM work_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 50)))

        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [parse_work_event(row) for row in rows]

    def stats(self) -> dict:
        chain_count = self.conn.execute("SELECT COUNT(*) FROM chains").fetchone()[0]
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        return {
            "chains": chain_count,
            "nodes": node_count,
            "db_path": self.db_path,
        }

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
