"""ChainMem — 链式 + 向量混合记忆系统"""

__version__ = "0.1.0"

from chainmem.core.node import ChainNode, Chain
from chainmem.store.sqlite_store import SQLiteStore
from chainmem.pipeline.ingester import Ingester
from chainmem.pipeline.retriever import Retriever


class ChainMemory:
    """ChainMem 主入口类"""

    def __init__(self, db_path: str = "~/.chainmem/data.db"):
        self.db_path = db_path
        self.store: SQLiteStore | None = None
        self.ingester: Ingester | None = None
        self.retriever: Retriever | None = None

    def open(self):
        """打开数据库，加载索引"""
        import os
        path = os.path.expanduser(self.db_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        self.store = SQLiteStore(path)
        self.store.initialize()
        self.ingester = Ingester(self.store)
        self.retriever = Retriever(self.store)
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

    def stats(self) -> dict:
        if not self.store:
            raise RuntimeError("Call .open() first")
        return self.store.stats()

    def __enter__(self):
        return self.open()

    def __exit__(self, *args):
        self.close()
