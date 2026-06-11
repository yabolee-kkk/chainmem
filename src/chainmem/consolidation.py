"""
ChainMem Consolidation — LLM-powered memory summarization and insight extraction.

Runs periodically in the background to:
  1. Cluster similar memory chains
  2. Generate summaries per cluster
  3. Extract new insights about the user
  4. Detect contradictions between old and new memories
  5. Mark stale/unreferenced chains

Results are stored back into ChainMem as tagged insight chains,
so they are discoverable via chainmem_search/retrieve.

Config (env vars):
  CHAINMEM_LLM_URL     — OpenAI-compatible API endpoint
                         (default: https://api.deepseek.com/v1/chat/completions)
  CHAINMEM_LLM_KEY    — API key
                         (default: DEEPSEEK_API_KEY env var, or empty = skip LLM)
  CHAINMEM_LLM_MODEL  — Model name
                         (default: deepseek-chat)
  CONSOLIDATE_INTERVAL — Seconds between runs (default: 14400 = 4h)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_LLM_URL = "https://api.deepseek.com/v1/chat/completions"
_DEFAULT_LLM_MODEL = "deepseek-chat"
_DEFAULT_INTERVAL = 14400  # 4 hours
_STATE_PATH = os.path.expanduser("~/.chainmem/consolidation.json")
_INSIGHT_TAGS = ("_consolidation", "_insight")
_CLUSTER_MIN_CHAINS = 3


# ---------------------------------------------------------------------------
# LLM caller (OpenAI-compatible, no extra deps)
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, system: str = "", config: dict = None) -> str:
    """Call an OpenAI-compatible LLM API and return the response text.

    config may contain: url, api_key, model
    """
    cfg = config or {}
    url = cfg.get("url", os.environ.get("CHAINMEM_LLM_URL", _DEFAULT_LLM_URL))
    api_key = cfg.get("api_key", os.environ.get("CHAINMEM_LLM_KEY", os.environ.get("DEEPSEEK_API_KEY", "")))
    model = cfg.get("model", os.environ.get("CHAINMEM_LLM_MODEL", _DEFAULT_LLM_MODEL))

    if not api_key:
        return ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.3,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        logger.warning("LLM call failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    """Load consolidation state from disk."""
    try:
        if os.path.exists(_STATE_PATH):
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Failed to load consolidation state: %s", e)
    return {
        "last_run_at": 0.0,
        "last_chain_cursor": 0,  # id of last processed chain
        "runs": 0,
        "clusters_found": 0,
        "insights_extracted": 0,
        "contradictions_detected": 0,
    }


def _save_state(state: dict):
    """Persist consolidation state."""
    try:
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("Failed to save consolidation state: %s", e)


# ---------------------------------------------------------------------------
# Core consolidation logic
# ---------------------------------------------------------------------------

def _get_recent_chains(db_path: str, cursor_id: int, limit: int = 100) -> List[dict]:
    """Fetch chains newer than the cursor."""
    import sqlite3
    chains = []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Get chains with rowid > cursor, excluding existing insight chains
        rows = conn.execute("""
            SELECT c.id, c.anchor_prefix, c.node_count, c.tags, c.created_at, c.rowid
            FROM chains c
            WHERE c.rowid > ?
              AND (c.tags IS NULL OR c.tags NOT LIKE '%_consolidation%')
            ORDER BY c.rowid ASC
            LIMIT ?
        """, (cursor_id, limit)).fetchall()

        for r in rows:
            ch = dict(r)
            # Get preview content (first node)
            node = conn.execute(
                "SELECT text FROM nodes WHERE chain_id=? ORDER BY seq LIMIT 1",
                (ch["id"],)
            ).fetchone()
            ch["preview"] = node["text"][:300] if node else ""
            # Get full content
            nodes = conn.execute(
                "SELECT text FROM nodes WHERE chain_id=? ORDER BY seq",
                (ch["id"],)
            ).fetchall()
            ch["content"] = "\n".join(n["text"] for n in nodes)[:2000]
            chains.append(ch)
        conn.close()
    except Exception as e:
        logger.error("Error fetching chains for consolidation: %s", e)
    return chains


def _build_clustering_prompt(chains: List[dict]) -> str:
    """Build a prompt that asks the LLM to cluster similar chains."""
    items = []
    for i, ch in enumerate(chains, 1):
        tags = ch.get("tags", "") or "无标签"
        preview = (ch.get("preview", "") or "")[:150]
        items.append(f"[{i}] 标签: {tags}\n    内容: {preview}")

    return (
        "以下是用户的记忆片段列表。请将它们按主题聚类（相似的放一起），"
        "并为每个聚类生成一句话摘要。\n\n"
        "要求：\n"
        "1. 只输出 JSON 格式，不输出其他文字\n"
        "2. 格式：{\"clusters\": [{\"name\": \"聚类名\", \"indices\": [1, 3, 5], \"summary\": \"摘要\"}]}\n"
        "3. indices 引用上面列表的编号\n"
        "4. 单个片段不聚类\n\n"
        + "\n\n".join(items)
    )


def _build_insight_prompt(cluster: dict, chains: List[dict]) -> str:
    """Build a prompt to extract insights from a cluster."""
    items = []
    for idx in cluster.get("indices", []):
        ch = chains[idx - 1]
        items.append(ch.get("content", "")[:800])

    return (
        "以下是用户关于同一主题的多段记忆。请分析并提取：\n"
        "1. 核心事实（用户明确说过什么）\n"
        "2. 模式（反复出现的做法）\n"
        "3. 偏好（明确或隐晦的倾向）\n\n"
        "输出格式：\n"
        "【核心事实】...\n"
        "【模式】...\n"
        "【偏好】...\n\n"
        + "\n---\n".join(items)
    )


def _build_contradiction_prompt(chains: List[dict]) -> str:
    """Check for contradictions across all new chains + previous insights."""
    items = []
    for ch in chains:
        items.append(f"[标签:{ch.get('tags','?')}] {ch.get('content','')[:500]}")
    if not items:
        return ""

    return (
        "以下记忆片段中是否存在矛盾？比如同一个配置的不同值、\n"
        "相互冲突的偏好、前后不一致的陈述。\n"
        "输出格式（无矛盾则输出空 JSON）：\n"
        "{\"contradictions\": [{\"a\": \"片段A摘要\", \"b\": \"片段B摘要\", \"detail\": \"矛盾说明\"}]}\n\n"
        + "\n\n".join(items)
    )


def _store_insight_chain(cm, text: str, insight_type: str, tags: str = "") -> None:
    """Store an insight back into ChainMem."""
    all_tags = f"{_INSIGHT_TAGS[0]},{_INSIGHT_TAGS[1]},{insight_type}"
    if tags:
        all_tags += f",{tags}"
    try:
        import numpy as np
        chain = cm.ingest(text.strip(), source="consolidation", tags=all_tags.split(","))
        nodes = chain.nodes
        if nodes:
            embeddings = np.array([n.embedding for n in nodes])
            cm.retriever.add_nodes(
                embeddings=embeddings,
                node_ids=[n.id for n in nodes],
                texts=[n.text for n in nodes],
                chain_ids=[n.chain_id for n in nodes],
                next_ids=[n.next_id for n in nodes],
                seqs=[n.seq for n in nodes],
                prev_ids=[n.prev_id for n in nodes],
            )
    except Exception as e:
        logger.warning("Failed to store insight chain: %s", e)


def run_consolidation(cm, db_path: str, llm_config: dict = None) -> dict:
    """Run one full consolidation cycle.

    Returns a dict with results summary.
    """
    state = _load_state()
    llm_cfg = llm_config or {}
    result = {
        "chains_scanned": 0,
        "clusters_found": 0,
        "insights_extracted": 0,
        "contradictions_detected": 0,
        "error": "",
    }

    # 1. Fetch recent chains
    chains = _get_recent_chains(db_path, state.get("last_chain_cursor", 0))
    if not chains:
        logger.info("Consolidation: no new chains to process")
        state["last_run_at"] = time.time()
        _save_state(state)
        result["chains_scanned"] = 0
        return result

    result["chains_scanned"] = len(chains)
    max_id = max(c.get("rowid", 0) for c in chains)

    logger.info("Consolidation: scanning %d chains...", len(chains))

    # 2. Cluster similar chains (LLM)
    use_llm = bool(llm_cfg.get("api_key") or os.environ.get("CHAINMEM_LLM_KEY") or os.environ.get("DEEPSEEK_API_KEY"))

    if use_llm and len(chains) >= _CLUSTER_MIN_CHAINS:
        # Cluster
        cluster_prompt = _build_clustering_prompt(chains)
        cluster_resp = _call_llm(cluster_prompt, "你是记忆分析专家，擅长从碎片信息中发现模式和关联。", llm_cfg)
        clusters = []
        if cluster_resp:
            # Parse JSON from response
            try:
                # Handle both pure JSON and text-wrapped JSON
                json_str = cluster_resp.strip()
                if "```json" in json_str:
                    json_str = json_str.split("```json")[1].split("```")[0].strip()
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].split("```")[0].strip()
                parsed = json.loads(json_str)
                clusters = parsed.get("clusters", [])
                # Only keep clusters with 3+ items
                clusters = [c for c in clusters if len(c.get("indices", [])) >= _CLUSTER_MIN_CHAINS]
            except (json.JSONDecodeError, KeyError) as e:
                logger.debug("Failed to parse cluster response: %s", e)

        result["clusters_found"] = len(clusters)

        # Extract insights per cluster
        for cluster in clusters:
            insight_resp = _call_llm(
                _build_insight_prompt(cluster, chains),
                "你是用户研究专家，从对话中提取可靠的模式、事实和偏好。只输出事实，不猜测。",
                llm_cfg,
            )
            if insight_resp and len(insight_resp.strip()) > 20:
                _store_insight_chain(
                    cm,
                    f"[聚类] {cluster.get('name', '未命名')}\n{insight_resp}",
                    insight_type="_cluster_summary",
                    tags=cluster.get("name", "未分类"),
                )
                result["insights_extracted"] += 1

        # Contradiction detection
        contrad_prompt = _build_contradiction_prompt(chains)
        if contrad_prompt:
            contrad_resp = _call_llm(
                contrad_prompt,
                "你是数据一致性检查员，严格基于给定文本判断矛盾，不添加不存在的信息。",
                llm_cfg,
            )
            if contrad_resp:
                try:
                    json_str = contrad_resp.strip()
                    if "```json" in json_str:
                        json_str = json_str.split("```json")[1].split("```")[0].strip()
                    elif "```" in json_str:
                        json_str = json_str.split("```")[1].split("```")[0].strip()
                    contrad_data = json.loads(json_str)
                    contradictions = contrad_data.get("contradictions", [])
                    result["contradictions_detected"] = len(contradictions)
                    for c in contradictions:
                        _store_insight_chain(
                            cm,
                            f"[矛盾] {c.get('detail', '')}\n  A: {c.get('a', '')}\n  B: {c.get('b', '')}",
                            insight_type="_contradiction",
                        )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug("Failed to parse contradiction response: %s", e)

    # 3. Update state
    state["last_run_at"] = time.time()
    state["last_chain_cursor"] = max_id
    state["runs"] = state.get("runs", 0) + 1
    state["clusters_found"] = state.get("clusters_found", 0) + result["clusters_found"]
    state["insights_extracted"] = state.get("insights_extracted", 0) + result["insights_extracted"]
    state["contradictions_detected"] = state.get("contradictions_detected", 0) + result["contradictions_detected"]
    _save_state(state)

    logger.info(
        "Consolidation complete: %d chains, %d clusters, %d insights, %d contradictions",
        result["chains_scanned"],
        result["clusters_found"],
        result["insights_extracted"],
        result["contradictions_detected"],
    )
    return result


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

class ConsolidationScheduler:
    """Periodically runs consolidation in a background thread."""

    def __init__(self, get_cm_fn, db_path: str, interval: int = _DEFAULT_INTERVAL):
        self._get_cm = get_cm_fn
        self._db_path = db_path
        self._interval = interval
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._last_result: dict = {}
        self._status = "idle"

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="consolidation")
        self._thread.start()
        logger.info("Consolidation scheduler started (interval=%ds)", self._interval)

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def trigger_now(self) -> dict:
        """Manually trigger a consolidation run (blocking)."""
        self._status = "running"
        try:
            cm = self._get_cm()
            llm_config = {
                "url": os.environ.get("CHAINMEM_LLM_URL", _DEFAULT_LLM_URL),
                "api_key": os.environ.get("CHAINMEM_LLM_KEY", os.environ.get("DEEPSEEK_API_KEY", "")),
                "model": os.environ.get("CHAINMEM_LLM_MODEL", _DEFAULT_LLM_MODEL),
            }
            result = run_consolidation(cm, self._db_path, llm_config)
            self._last_result = result
            self._status = "idle"
            return result
        except Exception as e:
            self._last_result = {"error": str(e)}
            self._status = "error"
            return self._last_result

    def get_status(self) -> dict:
        state = _load_state()
        return {
            "status": self._status,
            "last_run_at": state.get("last_run_at", 0),
            "runs": state.get("runs", 0),
            "total_clusters": state.get("clusters_found", 0),
            "total_insights": state.get("insights_extracted", 0),
            "total_contradictions": state.get("contradictions_detected", 0),
            "last_result": self._last_result,
            "interval": self._interval,
        }

    def _run_loop(self):
        state = _load_state()
        last_ts = state.get("last_run_at", 0)
        while not self._stop_event.is_set():
            now = time.time()
            if now - last_ts >= self._interval:
                self.trigger_now()
                last_ts = time.time()
            # Sleep 60s between checks (don't busy-wait)
            self._stop_event.wait(60)
