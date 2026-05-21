"""结链管道：文本 → 切块 → 嵌入 → 存储"""

from __future__ import annotations
import re
import uuid

import numpy as np
from sentence_transformers import SentenceTransformer

from chainmem.core.node import ChainNode, Chain
from chainmem.store.sqlite_store import SQLiteStore


# 全局复用嵌入模型（加载一次即可）
_MODEL: SentenceTransformer | None = None
_MODEL_NAME: str = "all-MiniLM-L6-v2"


def _get_model(model_name: str | None = None) -> SentenceTransformer:
    global _MODEL, _MODEL_NAME
    if model_name is not None and model_name != _MODEL_NAME:
        # 切换模型
        _MODEL = SentenceTransformer(model_name)
        _MODEL_NAME = model_name
    elif _MODEL is None:
        _MODEL = SentenceTransformer(_MODEL_NAME)
    return _MODEL


def set_model(model_name: str):
    """切换嵌入模型（下次调用 _get_model 时生效）"""
    global _MODEL_NAME, _MODEL
    _MODEL_NAME = model_name
    _MODEL = None


def chunk_text(text: str, max_chars: int = 18) -> list[str]:
    """将长文本按自然停顿切分为短语块

    切分规则：
      1. 始终按句号/问号/感叹号等终结标点切分
      2. 始终按逗号/顿号/冒号切分
      3. 过长的块（> max_chars）硬截断
    """
    # 1. 按终结标点切分（保留标点）
    parts = re.split(r'(?<=[。！？；…\n])\s*', text)
    parts = [p.strip() for p in parts if p.strip()]

    # 2. 对每个部分按逗号/顿号/冒号再切
    chunks = []
    for part in parts:
        sub = re.split(r'(?<=[，、：])\s*', part)
        for s in sub:
            s = s.strip()
            if not s:
                continue
            if len(s) <= max_chars:
                chunks.append(s)
            else:
                # 过长的硬截断
                for i in range(0, len(s), max_chars):
                    chunks.append(s[i:i + max_chars])
    # 3. 合併過短的塊（避免 sentence-transformers 的退化嵌入）
    chunks = merge_short_chunks(chunks)
    return [c for c in chunks if c]


def merge_short_chunks(chunks: list[str], min_chars: int = 6) -> list[str]:
    """合併過短的塊到前一個相鄰塊

    sentence-transformers 對 ≤5 字的短文本會產生退化嵌入
    （不同文本得到完全相同向量->cosine=1.0）
    因此需要將短塊併入相鄰的長塊
    """
    if len(chunks) <= 1:
        return chunks
    merged = []
    for chunk in chunks:
        if merged and len(chunk) <= min_chars:
            # 合併到前一個塊
            merged[-1] = merged[-1] + chunk
        else:
            merged.append(chunk)
    return merged


class Ingester:
    """结链器：文本 → 链"""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.embedder = _get_model()

    def ingest(self, text: str, source: str = "", tags: list[str] | None = None) -> Chain:
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("Empty text after chunking")

        chain_id = str(uuid.uuid4())
        nodes: list[ChainNode] = []

        # 1. 嵌入所有块
        embeddings = self.embedder.encode(chunks, normalize_embeddings=True)

        # 2. 创建节点，串联
        prev_id: str | None = None
        for i, (phrase_text, emb) in enumerate(zip(chunks, embeddings)):
            node_id = str(uuid.uuid4())
            node = ChainNode(
                id=node_id,
                chain_id=chain_id,
                seq=i + 1,
                text=phrase_text,
                embedding=emb,
                prev_id=prev_id,
            )
            if prev_id:
                # 更新前一个节点的 next_id
                nodes[-1].next_id = node_id
            nodes.append(node)
            prev_id = node_id

        root_id = nodes[0].id
        leaf_id = nodes[-1].id

        # 3. 存数据库
        self.store.save_chain(
            chain_id=chain_id,
            anchor_prefix=nodes[0].text_prefix,
            root_id=root_id,
            leaf_id=leaf_id,
            node_count=len(nodes),
            source=source,
            tags=tags or [],
        )
        for n in nodes:
            self.store.save_node(
                node_id=n.id,
                chain_id=n.chain_id,
                seq=n.seq,
                text=n.text,
                prev_id=n.prev_id,
                next_id=n.next_id,
            )

        chain = Chain.from_nodes(nodes)
        chain.source = source
        chain.tags = tags or []
        return chain
