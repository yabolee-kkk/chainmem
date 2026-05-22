"""SQLite 存储层——节点和链的持久化"""

from __future__ import annotations
import json
import sqlite3
from typing import Optional
import numpy as np


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
        """)
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
                  prev_id: str | None = None, next_id: str | None = None):
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, chain_id, seq, text, text_prefix, prev_id, next_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (node_id, chain_id, seq, text, text[:3],
             prev_id, next_id),
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
            "SELECT id, chain_id, seq, text, prev_id, next_id FROM nodes ORDER BY seq"
        ).fetchall()
        return [dict(r) for r in rows]

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
