"""追溯管道：前缀 → 最近邻 → 指针遍历 → 文本复原"""

from __future__ import annotations
import json
import os
import pickle
from typing import TYPE_CHECKING, Optional

import numpy as np

from chainmem.core.node import ChainNode
from chainmem.store.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    import faiss
    from sentence_transformers import SentenceTransformer
    from chainmem.core.crypto import Encryptor

# ── FAISS 索引持久化路径 ──
INDEX_DIR = os.path.expanduser("~/.chainmem")
FAISS_INDEX_PATH = os.path.join(INDEX_DIR, "faiss_index.bin")
FAISS_META_PATH = os.path.join(INDEX_DIR, "faiss_metadata.pkl")


# 复用嵌入模型


def _get_model():
    """获取嵌入模型，缺失时给出安装指引"""
    try:
        from chainmem.pipeline.ingester import _get_model as _ig
        return _ig()
    except ImportError:
        print(
            "❌ ChainMem 需要 sentence-transformers + faiss-cpu 来进行语义嵌入。\n"
            "   安装方法：\n"
            "     完整版（含 GPU torch，~1.5GB）：pip install chainmem[full]\n"
            "     CPU 版（轻量，192MB）：pip install torch --index-url https://download.pytorch.org/whl/cpu\n"
            "                           pip install sentence-transformers faiss-cpu\n"
            "   下载地址：\n"
            "     https://pypi.org/project/sentence-transformers/\n"
            "     https://pypi.org/project/faiss-cpu/\n",
            file=__import__("sys").stderr,
        )
        raise


class Retriever:
    """追溯器：查询 → 链遍历"""

    def __init__(self, store: SQLiteStore, encryptor: Optional["Encryptor"] = None):
        self.store = store
        self._embedder = None
        self.encryptor = encryptor
        self.index = None  # faiss.Index — 惰性导入
        self.id_to_text: dict[str, str] = {}       # node_id → text
        self.id_to_chain: dict[str, str] = {}       # node_id → chain_id
        self.id_to_next: dict[str, str | None] = {}  # node_id → next_id
        self.id_to_seq: dict[str, int] = {}         # node_id → seq
        self.id_list: list[str] = []                # idx → node_id
        self.chain_tags: dict[str, list[str]] = {}  # chain_id → tags
        self.id_to_encrypted: dict[str, bool] = {}   # node_id → encrypted flag
        self.id_to_encryption_iv: dict[str, str] = {}  # node_id → encryption IV
        self.id_to_prev: dict[str, str | None] = {}  # node_id → prev_id（双向遍历用）

    def _get_embedder(self):
        """惰性获取嵌入模型"""
        if self._embedder is None:
            self._embedder = _get_model()
        return self._embedder

    # ──────────────────────────────────────────
    # FAISS 索引持久化
    # ──────────────────────────────────────────

    def save_index(self):
        """将 FAISS 索引 + 元数据保存到磁盘"""
        if self.index is None or self.index.ntotal == 0:
            return
        import faiss
        os.makedirs(INDEX_DIR, exist_ok=True)
        faiss.write_index(self.index, FAISS_INDEX_PATH)
        with open(FAISS_META_PATH, "wb") as f:
            pickle.dump({
                "id_to_text": self.id_to_text,
                "id_to_chain": self.id_to_chain,
                "id_to_next": self.id_to_next,
                "id_to_seq": self.id_to_seq,
                "id_list": self.id_list,
                "chain_tags": self.chain_tags,
                "id_to_encrypted": self.id_to_encrypted,
                "id_to_encryption_iv": self.id_to_encryption_iv,
                "id_to_prev": self.id_to_prev,
            }, f, protocol=pickle.HIGHEST_PROTOCOL)

    def load_index(self) -> bool:
        """从磁盘加载 FAISS 索引 + 元数据。成功返回 True"""
        if not os.path.exists(FAISS_INDEX_PATH) or not os.path.exists(FAISS_META_PATH):
            return False
        try:
            import faiss
            self.index = faiss.read_index(FAISS_INDEX_PATH)
            with open(FAISS_META_PATH, "rb") as f:
                meta = pickle.load(f)
            self.id_to_text = meta["id_to_text"]
            self.id_to_chain = meta["id_to_chain"]
            self.id_to_next = meta["id_to_next"]
            self.id_to_seq = meta["id_to_seq"]
            self.id_list = meta["id_list"]
            self.chain_tags = meta["chain_tags"]
            self.id_to_encrypted = meta.get("id_to_encrypted", {})  # 向后兼容
            self.id_to_encryption_iv = meta.get("id_to_encryption_iv", {})  # 向后兼容
            self.id_to_prev = meta.get("id_to_prev", {})  # 向后兼容（v0.5.2+）
            return True
        except Exception as e:
            print(f"[chainmem] 加载 FAISS 索引失败，将重建: {e}", file=__import__("sys").stderr)
            return False

    # ──────────────────────────────────────────
    # 增量节点添加（ingest 后只编新节点）
    # ──────────────────────────────────────────

    def add_nodes(self, embeddings: np.ndarray,
                  node_ids: list[str],
                  texts: list[str],
                  chain_ids: list[str],
                  next_ids: list[str | None],
                  seqs: list[int],
                  encrypted_flags: list[bool] | None = None,
                  encryption_ivs: list[str] | None = None,
                  prev_ids: list[str | None] | None = None):
        """增量添加节点到 FAISS 索引（无需全量重建）"""
        import faiss
        dim = embeddings.shape[1]
        if self.index is None:
            self.index = faiss.IndexFlatIP(dim)
        elif self.index.d != dim:
            # 不匹配？重建
            self.rebuild_index(force=True)
            return

        self.index.add(embeddings.astype(np.float32))
        for i, nid in enumerate(node_ids):
            self.id_list.append(nid)
            self.id_to_text[nid] = texts[i]
            self.id_to_chain[nid] = chain_ids[i]
            self.id_to_next[nid] = next_ids[i]
            self.id_to_seq[nid] = seqs[i]
            if encrypted_flags:
                self.id_to_encrypted[nid] = encrypted_flags[i]
            if encryption_ivs:
                self.id_to_encryption_iv[nid] = encryption_ivs[i]
            if prev_ids:
                self.id_to_prev[nid] = prev_ids[i]

        # 持久化到磁盘
        self.save_index()

    # ──────────────────────────────────────────
    # 索引重建（首次启动 / 回退）
    # ──────────────────────────────────────────

    def rebuild_index(self, force: bool = False):
        """从 SQLite 重建 FAISS 索引和映射表（带磁盘缓存 + 增量跳过）

        启动顺序：
          1. 如果磁盘有持久化索引且节点数匹配 → 直接加载（~1s）
          2. 否则从 SQLite 全量重建 → 存盘
        """
        rows = self.store.get_all_nodes_with_embeddings_dense()
        if not rows:
            self.index = None
            return

        # 快速跳过：节点数没变且索引已存在
        if not force and self.index is not None and self.index.ntotal > 0:
            if len(rows) == len(self.id_list):
                return

        # 尝试从磁盘加载
        if not force and self.load_index():
            if len(rows) == len(self.id_list):
                return
            # 节点数变了 → 全量重建
            print(f"[chainmem] 节点数变动 ({len(self.id_list)} → {len(rows)})，重建索引",
                  file=__import__("sys").stderr)

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
        self.id_to_encrypted.clear()
        self.id_to_encryption_iv.clear()
        self.id_to_prev.clear()

        for row in rows:
            nid = row["id"]
            self.id_to_text[nid] = row["text"]
            self.id_to_chain[nid] = row["chain_id"]
            self.id_to_next[nid] = row["next_id"]
            self.id_to_seq[nid] = row["seq"]
            self.id_to_encrypted[nid] = bool(row.get("encrypted", 0))
            self.id_to_encryption_iv[nid] = row.get("encryption_iv", "") or ""
            self.id_to_prev[nid] = row.get("prev_id")

        # 重新嵌入所有文本
        texts = [self.id_to_text[nid] for nid in (r["id"] for r in rows)]
        import faiss
        embeddings = self._get_embedder().encode(texts, normalize_embeddings=True)

        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(embeddings.astype(np.float32))
        self.id_list = [r["id"] for r in rows]

        # 持久化到磁盘
        self.save_index()

    # ──────────────────────────────────────────
    # 检索
    # ──────────────────────────────────────────

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
        q_vec = self._get_embedder().encode([query], normalize_embeddings=True).astype(np.float32)

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

        # ── Step 2.5: 当 FAISS 最佳候选不含查询词时，尝试模糊兜底 ──
        tokens = self._tokenize_query(raw_query)
        if len(tokens) >= 2 and best_score < 0.7:
            top_node_text = self.id_to_text.get(start_id, "")
            if top_node_text:
                top_matches = sum(
                    1 for t in tokens if self._fuzzy_match(t, top_node_text)
                )
                if top_matches / len(tokens) < 0.5:
                    fallback_results = self._substring_fallback(
                        raw_query, tags=tags
                    )
                    if fallback_results:
                        return fallback_results

        # ── Step 3: 链遍历 ──
        results = self._traverse_forward(start_id, max_steps)
        return results

    def _substring_fallback(self, query: str,
                            tags: list[str] | None = None) -> list[str]:
        """纯子串匹配兜底 + 模糊分词兜底

        先尝试精确子串匹配（整句在节点中）。无结果时拆成词/字，
        按命中词数排序选最佳起点。提高短查询和重组查询的召回率。
        """
        if not query:
            return []
        matched_ids = []
        found_encrypted_no_key = False
        for nid in self.id_to_text:
            text = self._get_text(nid)
            if query in text:
                if tags:
                    chain_id = self.id_to_chain.get(nid, "")
                    node_tags = self.chain_tags.get(chain_id, [])
                    if not any(t in node_tags for t in tags):
                        continue
                matched_ids.append(nid)
            elif self.id_to_encrypted.get(nid, False) and (
                self.encryptor is None or not self.encryptor.available
            ):
                found_encrypted_no_key = True

        # ── 精确匹配无结果 → 模糊分词兜底 ──
        if not matched_ids:
            tokens = self._tokenize_query(query)
            if len(tokens) >= 2:
                scored: list[tuple[float, str]] = []
                for nid in self.id_to_text:
                    text = self._get_text(nid)
                    if not text:
                        continue
                    # 标签过滤
                    if tags:
                        chain_id = self.id_to_chain.get(nid, "")
                        node_tags = self.chain_tags.get(chain_id, [])
                        if not any(t in node_tags for t in tags):
                            continue
                    # 统计命中词数（含编辑距离兜底）
                    hits = sum(1 for t in tokens if self._fuzzy_match(t, text))
                    if hits > 0:
                        scored.append((hits / max(len(tokens), 1), nid))

                if scored:
                    scored.sort(key=lambda x: -x[0])
                    match_ratio = scored[0][0]
                    matched_ids.append(scored[0][1])

        if not matched_ids:
            if found_encrypted_no_key:
                return ["[🔒 存在匹配的加密记忆，请配置 CHAINMEM_KEY 解密]"]
            return []
        # 按匹配质量排序，选最佳起点遍历
        start_id = matched_ids[0]
        return self._traverse_forward(start_id, 100)

    @staticmethod
    def _tokenize_query(query: str) -> list[str]:
        """将查询拆成搜索用词元

        策略：
        - 按空格/标点拆分英文和中英混合词
        - 对中文连续段提取 2-gram（覆盖不靠空格分隔的中文）
        - 去重，过滤单字符
        - 保留原查询词（供整体匹配用）
        """
        import re
        tokens = set()

        # 1. 按空格/标点拆分
        for t in re.split(r'[\s,，。；：！？、()（）""''【】《》/\'\"\\s]+', query):
            t = t.strip()
            if len(t) >= 2:
                tokens.add(t)

        # 2. 中文 2-gram（覆盖"健忘问题"→"健忘"+"忘问"+"问题"这种）
        for i in range(len(query) - 1):
            sub = query[i:i+2]
            if sub[0] >= '\u4e00' and sub[0] <= '\u9fff' and \
               sub[1] >= '\u4e00' and sub[1] <= '\u9fff':
                tokens.add(sub)

        return list(tokens)

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        """计算编辑距离（迭代优化版，O(n) 空间）"""
        if len(s1) < len(s2):
            s1, s2 = s2, s1
        if not s2:
            return len(s1)
        prev_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev_row[j + 1] + 1
                deletions = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row
        return prev_row[-1]

    def _fuzzy_match(self, token: str, text: str, max_dist: int = 2) -> bool:
        """近似词匹配：先试精确子串，失败后用编辑距离比对文本中的长词

        只在 token ≥5 字符时启用，避免短词（如 'in' 'to'）被错误匹配。
        max_dist=2 允许：1 个字母替换 + 1 个字母增删 的拼写错误。
        """
        if token in text:
            return True
        if len(token) < 5:
            return False
        import re
        for word in re.findall(r'\w{4,}', text):
            if self._levenshtein(token, word) <= max_dist:
                return True
        return False

    def _get_text(self, node_id: str) -> str:
        """获取节点文本，加密时透明解密"""
        text = self.id_to_text.get(node_id, "")
        if not text:
            return text
        encrypted = self.id_to_encrypted.get(node_id, False)
        if not encrypted:
            return text
        # 加密节点：尝试解密
        if self.encryptor is not None and self.encryptor.available:
            try:
                iv = self.id_to_encryption_iv.get(node_id, "")
                return self.encryptor.decrypt(text, iv)
            except Exception:
                return "[🔒 加密内容（解密失败）]"
        else:
            return "[🔒 加密内容（需配置 CHAINMEM_KEY）]"

    def _traverse_bidirectional(self, start_id: str, max_steps: int = 100) -> list[str]:
        """从 start_id 开始，沿 prev_id 向后 + next_id 向前双向遍历

        先收集 start 之前的所有节点（\沿 prev_id 回溯到链头），
        然后收集 start 及之后的所有节点（沿 next_id 走到链尾）。
        确保匹配节点周围的上\下文也被完整保留。
        """
        texts: list[str] = []
        visited = set()

        # 1. 向后遍历（沿 prev_id 收集到链头 → 反转）
        backward: list[str] = []
        current_id: str | None = self.id_to_prev.get(start_id)
        for _ in range(max_steps):
            if current_id is None or current_id in visited:
                break
            visited.add(current_id)
            text = self._get_text(current_id)
            if not text:
                break
            backward.append(text)
            current_id = self.id_to_prev.get(current_id)

        # 反转向后收集的文本（从链头到 start 的前一个节点）
        texts.extend(reversed(backward))

        # 2. 向前遍历（从 start 沿 next_id 到链尾）
        current_id = start_id
        for _ in range(max_steps):
            if current_id is None or current_id in visited:
                break
            visited.add(current_id)

            text = self._get_text(current_id)
            if text is None or not text:
                break
            texts.append(text)

            chain_id = self.id_to_chain.get(current_id)
            if chain_id:
                self.store.update_chain_access(chain_id)

            current_id = self.id_to_next.get(current_id)

        return texts

    def _traverse_forward(self, start_id: str, max_steps: int) -> list[str]:
        """向前遍历（原实现，保持向后兼容，新代码请用 _traverse_bidirectional）"""
        return self._traverse_bidirectional(start_id, max_steps)
