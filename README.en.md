<p align="center">
  <img src="assets/logo.svg" width="200" alt="ChainMem Logo">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/github/v/release/yabolee-kkk/chainmem" alt="GitHub Release">
  <img src="https://img.shields.io/github/stars/yabolee-kkk/chainmem" alt="GitHub Stars">
  <img src="https://img.shields.io/pypi/dm/chainmem?color=blue&label=PyPI%20Downloads" alt="PyPI Downloads">
  <img src="https://img.shields.io/github/actions/workflow/status/yabolee-kkk/chainmem/ci.yml?branch=main&label=CI" alt="CI">
</p>

<h1 align="center">🧵 ChainMem</h1>
<p align="center"><b>Chain + Vector Hybrid Memory System — Giving AI Human-like Associative Memory</b></p>

<p align="center">
  🌐 <a href="README.md">中文</a> · English
</p>

---

## 🧠 Why ChainMem?

### How human memory works

> You hear the intro of a song → you recall the whole song → you remember that summer → you remember that person...

Human memory is **chain-like** — a single clue triggers an entire, complete memory. You don't "search" the past. You just need a **thread**.

### How AI memory works today

Existing AI memory systems (vector databases) work like a **library catalog**:

```
Search "stock" → returns 10 isolated fragments:
  [Fragment A] "...stock..."
  [Fragment B] "...stock investment..."
  [Fragment C] "...about stocks..."
  ...
  But can't reconstruct "what was actually said"
```

Each memory is an isolated **point** — you can find fragments, but you **can't piece together a complete, coherent memory**.

### ChainMem's answer

ChainMem uses **chain structure** to simulate human associative memory:

```
Search "stock" → find chain head → traverse pointers → full conversation reconstructed:
  "Actually my idea is stock investment..."
  "Should diversify risk"
  "So I picked consumer, tech, healthcare sectors..."
  "Allocation ratio is roughly..."
```

**Not searching for fragments — restoring entire memories.**

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 🧵 **Chain Memory** | Bidirectional pointer chains — pull one thread, get the whole story |
| 🔍 **Semantic Search** | FAISS vector retrieval for fuzzy matching |
| 🎯 **Substring Matching** | Exact match for short queries |
| ⚡ **Millisecond Latency** | Query ~22ms, incremental add ~132ms |
| 🚀 **Instant Startup** | FAISS index persistence — 60s → 1s |
| 🏷️ **Tag Classification** | Organize memories by project/topic |
| 🔌 **MCP Protocol** | Native integration with Hermes Agent and other AI Agents |
| 🐍 **Python SDK** | One-liner integration into your project |

---

## ⚡ Quick Start

### Installation

ChainMem uses a **layered dependency** design — choose what fits your needs:

| Level | Command | Size | Features |
|:------|:--------|:----:|:---------|
| 🪶 **Core** | `pip install chainmem` | ~22 KB | CLI + Python SDK (no sentence-transformers) |
| 🔍 **Full** | `pip install chainmem[full]` | ~1.5~2 GB | Semantic search + FAISS index |
| 🔐 **Secure** | `pip install chainmem[secure]` | ~5 MB | Auto-detect credentials + Fernet encryption |
| 🚀 **Full+Secure** | `pip install chainmem[full,secure]` | ~1.5~2 GB | Everything |
| 🐍 **Source** | `git clone ... && pip install -e .` | ~20 KB code | Install deps yourself |

> **💡 Tip:** Start with core to try it out. Install the full version when you need semantic search.
>
> **💡 Slow network?** `pip install chainmem[full]` pulls CUDA torch (~1GB). Use CPU-only instead:
> ```bash
> pip install chainmem
> pip install torch --index-url https://download.pytorch.org/whl/cpu    # CPU-only ~192MB
> pip install sentence-transformers faiss-cpu
> ```

#### 🪶 Core (recommended for first try)

```bash
pip install chainmem
```

Downloads in seconds. Supports:
- Full CLI (ingest / retrieve / stats / serve)
- SQLite persistence
- FTS5 text search
- MCP server
- **Semantic search requires additional deps (see below)**

#### 🔥 Full (enables semantic search)

```bash
pip install chainmem[full]
```

Auto-installs sentence-transformers and faiss-cpu (~1.5~2 GB). Adds:
- FAISS vector semantic search
- High-precision hybrid retrieval (semantic + substring + tags)

#### 📦 Manual dependency install

If your network is slow or you want to control versions:

```bash
pip install chainmem
pip install sentence-transformers>=3.0
pip install faiss-cpu>=1.8
```

Download URLs (for offline install):

| Dependency | PyPI URL | Size |
|:-----------|:---------|:----:|
| sentence-transformers | https://pypi.org/project/sentence-transformers/ | ~500 MB |
| faiss-cpu | https://pypi.org/project/faiss-cpu/ | ~30 MB |
| transformers | https://pypi.org/project/transformers/ | ~300 MB |
| torch | https://pypi.org/project/torch/ | ~800 MB |

```bash
# Offline install example
pip install sentence_transformers-3.x.x-py3-none-any.whl
pip install faiss_cpu-1.x.x-cp311-cp311-manylinux_2_17_x86_64.whl
```

> **Note:** Calling `ingest()` or `retrieve()` without the full deps will print installation instructions. CLI basics and MCP server work fine with just the core.

### Python SDK

```python
from chainmem import ChainMemory

# Initialize
cm = ChainMemory(db_path="~/.chainmem/data.db").open()

# Ingest: store a conversation as memory
chain = cm.ingest(
    "Actually my idea is to turn every memory including every conversation into a chain",
    source="demo",
    tags=["discussion", "memory", "architecture"],
)
print(f"Chain ID: {chain.id}, Nodes: {chain.node_count}")

# Retrieve: input a few words, get the full memory
result = cm.retrieve("Actually my idea")
print("".join(result))  # → Complete memory restored

# Stats
print(cm.stats())

cm.close()
```

### CLI Mode

```bash
# Ingest
chainmem ingest "Actually my idea is to turn every memory into a chain" --source demo --tags discussion,memory

# Retrieve
chainmem retrieve "Actually my idea"

# Stats
chainmem stats

# Start MCP server (for AI Agent integration)
chainmem serve --socket /tmp/chainmem.sock
```

---

## 🔬 How It Works

### Ingestion

![ChainMem Architecture](assets/architecture-preview.png)

```
  Raw Text
      │
      ▼
  ┌─────────────┐
  │  Chunking    │  Split by punctuation into phrases (6-18 chars)
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  Embedding   │  sentence-transformers encoding
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐     ┌──────────────────┐
  │  Chain Link  │────▶│  Node_A → Node_B │
  │  prev/next   │     │      → Node_C    │
  │  pointers    │     └──────────────────┘
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  SQLite      │  Zero-dependency persistence
  └─────────────┘
```

### Retrieval

```
  User Query ("stock")
      │
      ▼
  ┌─────────────┐     ┌──────────────────┐
  │  FAISS       │     │  Substring Match  │
  │  Semantic    │────▶│  +0.20 bonus      │
  │  Top-10      │     └──────────────────┘
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  Candidate   │  semantic + substring + tag scores
  │  Ranking     │
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐     ┌──────────────────┐
  │  Chain       │────▶│  Node_A → Node_B │
  │  Traversal   │     │      → Node_C    │
  │  next_id     │     │  → Full output   │
  └─────────────┘     └──────────────────┘
```

---

## 📊 Performance

| Metric | Traditional Vector Search | ChainMem |
|:-------|:------------------------:|:--------:|
| **Query Speed** | ~50ms | **~22ms** |
| **Result Completeness** | Fragments | **100% Original** |
| **Startup Time** | ~60s (full rebuild) | **~1s** (disk load) |
| **Incremental Add** | Full rebuild | **~132ms** (new nodes only) |
| **Chinese Support** | Poor | **Excellent** (trigram FTS5) |
| **Search Precision** | Semantic only | **Semantic + substring + tags** |

---

## 🔧 AI Agent Integration

### Hermes Agent (MCP Protocol)

Configure `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  chainmem:
    command: chainmem
    args: ["serve", "--socket", "/tmp/chainmem.sock"]
```

Three tools available after startup:

| Tool | Function |
|:-----|:---------|
| `chainmem_ingest(text, source, tags)` | Store memory |
| `chainmem_retrieve(query, tags)` | Retrieve memory (with tag filtering) |
| `chainmem_stats()` | View memory statistics |

---

## 🗺️ Roadmap

```
Phase 1 ✅ Core Loop
  ├─ Ingestion (text → chunk → embed → store)
  ├─ Retrieval (semantic + substring + chain traversal)
  ├─ Persistent MCP server
  ├─ FAISS index persistence (1s startup)
  └─ Incremental indexing (ms-level add)

Phase 2 🏗️ Human-like Memory
  ├─ Forgetting Curve
  ├─ Associative Enhancement (related chains on retrieval)
  ├─ Auto-ingestion (remember during conversation)
  └─ Branch disambiguation (same prefix, different context)

Phase 3 🎯 True "Brain Memory"
  ├─ Layered memory (working + short-term + long-term)
  ├─ Memory consolidation (multiple memories → knowledge)
  ├─ Cross-agent memory sharing
  └─ Sleep compaction (like human brain memory sorting)
```

---

## 🤝 Contributing

We welcome all contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

**Beginner-friendly tasks:**
- 📖 Improve documentation and examples
- 🐛 Fix bugs
- ✅ Add test coverage
- 🌍 Internationalization (i18n)
- 💡 Suggest new features

---

## 📄 License

[MIT License](LICENSE) © 2025 yabolee-kkk

---

<p align="center">
  <b>ChainMem — Make AI remember, like humans do.</b><br>
  <a href="https://github.com/yabolee-kkk/chainmem">GitHub</a> ·
  <a href="https://github.com/yabolee-kkk/chainmem/issues">Report Issues</a> ·
  <a href="https://github.com/yabolee-kkk/chainmem/discussions">Discussions</a>
</p>
