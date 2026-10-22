"""ChainMem — 链式 + 向量混合记忆系统"""

__version__ = "0.1.0"

from chainmem.core.node import ChainNode, Chain
from chainmem.store.sqlite_store import SQLiteStore
from chainmem.pipeline.ingester import Ingester
from chainmem.pipeline.retriever import Retriever


class ChainMemory:
    """ChainMem 主入口类"""

    def __init__(self, db_path: str = "~/.chainmem/data.db",
                 key: str | None = None):
        self.db_path = db_path
        self._key = key
        self._encryptor = None
        self.store: SQLiteStore | None = None
        self.ingester: Ingester | None = None
        self.retriever: Retriever | None = None

    def open(self):
        """打开数据库，加载索引"""
        import os
        path = os.path.expanduser(self.db_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # 初始化加密器（可选）
        from chainmem.core.crypto import Encryptor
        self._encryptor = Encryptor(key=self._key)
        if not self._encryptor.available and self._encryptor.reason:
            import sys
            print(f"[chainmem] 🔑 {self._encryptor.reason}", file=sys.stderr)

        self.store = SQLiteStore(path)
        self.store.initialize()
        self.ingester = Ingester(self.store, encryptor=self._encryptor)
        self.retriever = Retriever(self.store, encryptor=self._encryptor)
        return self

    def close(self):
        if self.store:
            self.store.close()

    def ingest(self, text: str, source: str = "", tags: list[str] | None = None) -> Chain:
        """结链：文本 → 切块 → 嵌入 → 存储"""
        if not self.ingester:
            raise RuntimeError("Call .open() first")
        return self.ingester.ingest(text, source=source, tags=tags or [])

    def set_model(self, model_name: str):
        """切换嵌入模型"""
        from chainmem.pipeline.ingester import set_model as _set
        _set(model_name)
        # 重建索引使新模型生效
        if self.retriever:
            self.retriever.rebuild_index()
        return self

    def retrieve(self, query: str, max_steps: int = 100,
                 tags: list[str] | None = None) -> list[str]:
        """追溯：查询 → 最近邻 → 指针遍历"""
        if not self.retriever:
            raise RuntimeError("Call .open() first")
        return self.retriever.retrieve(query, max_steps=max_steps, tags=tags)

    def record_work_event(self, **kwargs) -> dict:
        """Record a structured work event and link it to a semantic chain."""
        from chainmem.work_memory import normalize_work_event, redact_secrets

        if not self.store or not self.retriever:
            raise RuntimeError("Call .open() first")

        raw_text = kwargs.pop("raw_text", None)
        event = normalize_work_event(**kwargs)
        existing = self.store.find_work_event(
            event["agent_id"],
            event["session_id"],
            event["content_hash"],
        )
        if existing is not None:
            return existing

        summary_text = (
            f"Agent {event['agent_id']} 在项目 {event['project']} 中"
            f"完成了工作：{event['task_summary']}。"
            f"状态：{event['status']}。"
            f"产出：{', '.join(event['artifacts']) or '无'}。"
        )
        chain_text = raw_text or summary_text
        chain_text, _ = redact_secrets(chain_text)
        chain_tags = [
            "work-memory",
            f"agent:{event['agent_id']}",
            f"status:{event['status']}",
        ]
        if event["project"]:
            chain_tags.append(event["project"])
        chain_tags.extend(event["tags"])

        chain = self.ingest(
            chain_text,
            source=f"work-memory:{event['id']}",
            tags=chain_tags,
        )
        event["raw_chain_id"] = chain.id
        saved = self.store.save_work_event(event)
        self.retriever.rebuild_index(force=True)
        return saved

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
        from datetime import datetime, timedelta, timezone

        if not self.store or not self.retriever:
            raise RuntimeError("Call .open() first")
        if not 1 <= days <= 365:
            raise ValueError("days must be between 1 and 365")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")

        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        candidates = self.store.list_work_events(
            since=since,
            agent_id=agent_id,
            project=project,
            status=status,
            limit=50,
            include_private_for=include_private_for,
        )
        if not candidates or not query.strip():
            return candidates[:limit]

        matches = self.retriever.retrieve_chain_matches(
            query,
            tags=["work-memory"],
            limit=max(limit * 4, 10),
        )
        score_by_chain = dict(matches)
        ranked = []
        for event in candidates:
            score = score_by_chain.get(event.get("raw_chain_id"), 0.0)
            if score > 0:
                ranked.append({**event, "score": score})

        if not ranked:
            needle = query.strip().lower()
            ranked = [
                {**event, "score": 0.0}
                for event in candidates
                if needle in event["task_summary"].lower()
            ]

        ranked.sort(
            key=lambda item: (item["score"], item["occurred_at"]),
            reverse=True,
        )
        return ranked[:limit]

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
        from datetime import datetime, timedelta, timezone

        if not self.store:
            raise RuntimeError("Call .open() first")
        if not 1 <= days <= 365:
            raise ValueError("days must be between 1 and 365")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        since = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return self.store.list_work_events(
            since=since,
            agent_id=agent_id,
            project=project,
            status=status,
            limit=limit,
            include_private_for=include_private_for,
        )

    def get_work_event(
        self,
        event_id: str,
        *,
        include_private_for: str | None = None,
    ) -> dict | None:
        if not self.store:
            raise RuntimeError("Call .open() first")
        event = self.store.get_work_event(event_id)
        if event is None:
            return None
        if (
            event["visibility"] == "private"
            and event["agent_id"] != include_private_for
        ):
            return None
        return event

    def stats(self) -> dict:
        if not self.store:
            raise RuntimeError("Call .open() first")
        return self.store.stats()

    def __enter__(self):
        return self.open()

    def __exit__(self, *args):
        self.close()
