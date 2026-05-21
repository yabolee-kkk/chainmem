"""追溯管道：前缀 → 最近邻 → 指针遍历 → 文本复原"""

from __future__ import annotations
import json
import os
import pickle

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

from chainmem.core.node import ChainNode
from chainmem.store.sqlite_store import SQLiteStore


# 复用嵌入模型
_MODEL: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        from chainmem.pipeline.ingester import _get_model as _ig
        return _ig()
    return _MODEL


class Retriever:
    """追溯器：查询 → 链遍历"""

    def __init__(self, store: SQLiteStore):
        self.store = store
        self.embedder = _get_model()
        self.index: faiss.Index | None = None
        self.id_to_text: dict[str, str] = {}       # node_id → text
        self.id_to_chain: dict[str, str] = {}       # node_id → chain_id
        self.id_to_next: dict[str, str | None] = {}  # node_id → next_id
        self.id_to_seq: dict[str, int] = {}         # node_id → seq
        self.id_list: list[str] = []                # idx → node_id
        self.chain_tags: dict[str, list[str]] = {}  # chain_id → tags

    def rebuild_index(self):
        """从 SQLite 重建 FAISS 索引和映射表（每次 ingest 后调用）

        轻量跳过：如果节点数没变，跳过重建（serve 常驻模式优化）
        """
        rows = self.store.get_all_nodes_with_embeddings_dense()
        if not rows:
            self.index = None
            return

        # 快速跳过：如果节点数没变且索引已存在，不重建
        if self.index is not None and self.index.ntotal > 0:
            if len(rows) == len(self.id_list):
                return

        # 加载链标签
        self.chain_tags.clear()
        for chain_row in self.store.get_all_chains():
            cid = chain_row["id"]
            tags = chain_row.get("tags", [])
            if isinstance(tags, str):
                tags = json.loads(tags)
            self.chain_tags[cid] = tags or []

        # 从 SQLite 读取所有文本→id 映射
        self.id_to_text.clear()
        self.id_to_chain.clear()
        self.id_to_next.clear()
        self.id_to_seq.clear()

        for row in rows:
            nid = row["id"]
            self.id_to_text[nid] = row["text"]
            self.id_to_chain[nid] = row["chain_id"]
            self.id_to_next[nid] = row["next_id"]
            self.id_to_seq[nid] = row["seq"]

        # 重新嵌入所有文本
        texts = [self.id_to_text[nid] for nid in (r["id"] for r in rows)]
        embeddings = self.embedder.encode(texts, normalize_embeddings=True)

        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(embeddings.astype(np.float32))
        self.id_list = [r["id"] for r in rows]

    def retrieve(self, query: str, max_steps: int = 100,
                 tags: list[str] | None = None) -> list[str]:
        """追溯主入口——混合检索：语义（FAISS）+ 子串（文本匹配）+ 标签过滤

        搜索策略：
          1. FAISS 语义搜索获取 TOP-10 候选
          2. 如果指定了 tags，过滤掉不匹配的链
          3. 如果查询文本原样出现在某个节点中，给该节点额外加分
          4. 结合语义分和子串分，选择最佳起点
          5. 沿 next_id 链遍历输出完整记忆

        参数：
          query: 查询文本
          max_steps: 最大遍历步数
          tags: 可选，只返回包含这些标签的链（OR 逻辑，匹配任一即返回）

        返回：链上所有节点的文本列表（按 seq 排序）
        """
        if self.index is None or self.index.ntotal == 0:
            self.rebuild_index()
            if self.index is None or self.index.ntotal == 0:
                return []

        # 保存原始查询（用于子串匹配）
        raw_query = query.strip()

        # 短查询补齐：≤3字重复一次改善嵌入质量
        if len(raw_query) <= 3:
            query = raw_query + " " + raw_query
        else:
            query = raw_query

        # ── Step 1: FAISS 语义搜索 ──
        q_vec = self.embedder.encode([query], normalize_embeddings=True).astype(np.float32)

        top_k = min(10, self.index.ntotal)
        scores, indices = self.index.search(q_vec, top_k)
        best_score = float(scores[0][0])

        if best_score < 0.4 and not tags:
            # 语义相似度太低，试试纯子串匹配兜底
            return self._substring_fallback(raw_query, tags=tags)

        # ── Step 2: 子串匹配加分 + 标签过滤 ──
        candidates: list[tuple[float, str]] = []  # (combined_score, node_id)
        substring_bonus = 0.20  # 子串匹配加分
        tag_bonus = 0.15       # 标签命中加分

        for i in range(top_k):
            idx = int(indices[0][i])
            score = float(scores[0][i])
            node_id = self.id_list[idx]
            node_text = self.id_to_text.get(node_id, "")
            chain_id = self.id_to_chain.get(node_id, "")

            # 标签过滤：如果指定 tags，跳过不匹配的链
            if tags:
                node_tags = self.chain_tags.get(chain_id, [])
                if not any(t in node_tags for t in tags):
                    continue  # 没有匹配的标签，跳过此节点

                # 标签匹配加分
                if any(t in node_tags for t in tags):
                    score += tag_bonus

            combined = score
            if raw_query in node_text:
                combined += substring_bonus

            candidates.append((combined, node_id))

        if not candidates:
            # 标签过滤后无候选，试子串兜底
            return self._substring_fallback(raw_query, tags=tags)

        # 按综合分排序
        candidates.sort(key=lambda x: -x[0])
        best_combined_score, start_id = candidates[0]

        # ── Step 3: 链遍历 ──
        results = self._traverse_forward(start_id, max_steps)
        return results

    def _substring_fallback(self, query: str,
                            tags: list[str] | None = None) -> list[str]:
        """纯子串匹配兜底：当语义搜索低于阈值时使用"""
        if not query:
            return []
        # 查找包含查询文本的节点
        matched_ids = []
        for nid, text in self.id_to_text.items():
            if query in text:
                # 标签过滤
                if tags:
                    chain_id = self.id_to_chain.get(nid, "")
                    node_tags = self.chain_tags.get(chain_id, [])
                    if not any(t in node_tags for t in tags):
                        continue
                matched_ids.append(nid)
        if not matched_ids:
            return []
        # 选第一条匹配链的第一个节点开始遍历
        start_id = matched_ids[0]
        return self._traverse_forward(start_id, 100)

    def _traverse_forward(self, start_id: str, max_steps: int) -> list[str]:
        """从 start_id 开始，沿 next_id 向前遍历"""
        texts: list[str] = []
        current_id: str | None = start_id
        visited = set()

        for _ in range(max_steps):
            if current_id is None or current_id in visited:
                break
            visited.add(current_id)

            text = self.id_to_text.get(current_id)
            if text is None:
                break
            texts.append(text)

            # 更新访问统计
            chain_id = self.id_to_chain.get(current_id)
            if chain_id:
                self.store.update_chain_access(chain_id)

            current_id = self.id_to_next.get(current_id)

        return texts
