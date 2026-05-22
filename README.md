<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License">
  <img src="https://img.shields.io/github/v/release/yabolee-kkk/chainmem" alt="GitHub Release">
  <img src="https://img.shields.io/github/stars/yabolee-kkk/chainmem" alt="GitHub Stars">
</p>

<h1 align="center">🧵 ChainMem（链忆）</h1>
<p align="center"><b>链式 + 向量混合记忆系统 — 让 AI 拥有像人一样的联想式回忆</b></p>

<p align="center">
  <i>你给它一个"线头"，它还你一件"毛衣"。</i>
</p>

---

## 🧠 为什么需要 ChainMem？

### 人的记忆是什么样？

> 你听到一首歌的前奏 → 想起整首歌 → 想起那个夏天 → 想起那个人...

人的记忆是**链式的**——一个线索能引出整段完整的记忆。你不需要"搜索"过去，你只需要一个**线头**。

### AI 的记忆是什么样？

现有的 AI 记忆系统（向量数据库）本质上是**图书馆模式**：

```
你搜"股票" → 返回 10 个孤立片段：
  [片段A] "...股票..."
  [片段B] "...股票投资..."
  [片段C] "...关于股票..."
  ...
  但凑不出"当时到底说了什么"
```

每条记忆是独立的**点**——能搜到碎片，但**凑不出一段完整的、前后连贯的记忆**。

### ChainMem 的答案

ChainMem 用**链式结构**模拟人脑的联想回忆：

```
你搜"股票" → 找到链头 → 沿指针遍历 → 整段对话一字不差重现：
  "其实我的想法是股票投资..."
  "应该分散风险"
  "所以我选了消费、科技、医疗三个板块..."
  "每个板块配比大约是..."
```

**不是搜索碎片，而是还原整段记忆。**

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🧵 **链式记忆** | 双向指针串联，线头一扯整件毛衣出来 |
| 🔍 **语义搜索** | FAISS 向量检索兜底，记不清原话也能搜到 |
| 🎯 **子串匹配** | 短查询也能精确命中 |
| ⚡ **毫秒级响应** | 查询 ~22ms，增量添加 ~132ms |
| 🚀 **秒级启动** | FAISS 索引持久化，重启从 60s → 1s |
| 🏷️ **标签分类** | 按项目/主题组织记忆 |
| 🔌 **MCP 协议** | 原生支持 Hermes Agent 等 AI Agent 集成 |
| 🐍 **Python SDK** | 一句话集成到你的项目 |

---

## ⚡ 快速开始

```bash
pip install chainmem
```

```python
from chainmem import ChainMemory

# 初始化
cm = ChainMemory(db_path="~/.chainmem/data.db").open()

# 结链：把一段对话存为记忆
chain = cm.ingest(
    "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条",
    source="demo",
    tags=["讨论", "记忆系统", "架构"],
)
print(f"链 ID: {chain.id}, 节点数: {chain.node_count}")

# 追溯：输入几个字，拉出整段记忆
result = cm.retrieve("其实我的想法")
print("".join(result))  # → 完整记忆复原

# 统计
print(cm.stats())

cm.close()
```

### CLI 模式

```bash
# 结链
chainmem ingest "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条" --source demo --tags 讨论,记忆系统

# 追溯
chainmem retrieve "其实我的想法"

# 统计
chainmem stats

# 启动 MCP 服务器（用于 AI Agent 集成）
chainmem serve --socket /tmp/chainmem.sock
```

---

## 🔬 工作原理

### 结链（Ingestion）

```
  原始文本
      │
      ▼
  ┌─────────────┐
  │  语义切块     │  按标点/语义切成短语块（6-18字）
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  向量嵌入     │  sentence-transformers 编码
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐     ┌──────────────────┐
  │  链式串联     │────▶│  Node_A → Node_B │
  │  prev/next   │     │      → Node_C    │
  │  双向指针     │     └──────────────────┘
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐
  │  SQLite 存储  │  零依赖持久化
  └─────────────┘
```

### 追溯（Retrieval）

```
  用户查询（"股票"）
      │
      ▼
  ┌─────────────┐     ┌──────────────────┐
  │  FAISS 语义   │     │  子串精确匹配      │
  │  搜索 (TOP10) │────▶│  +0.20 加分       │
  └──────┬──────┘     └──────────────────┘
         │
         ▼
  ┌─────────────┐
  │  候选排序     │  语义分 + 子串分 + 标签分
  └──────┬──────┘
         │
         ▼
  ┌─────────────┐     ┌──────────────────┐
  │  指针链遍历    │────▶│  Node_A → Node_B │
  │  next_id     │     │      → Node_C    │
  │  忠实还原     │     │  → 完整对话输出    │
  └─────────────┘     └──────────────────┘
```

---

## 📊 性能对比

| 指标 | 传统向量搜索 | ChainMem |
|:-----|:----------:|:--------:|
| **检索速度** | ~50ms | **~22ms** |
| **结果完整性** | 碎片 | **100% 原始记忆** |
| **启动时间** | ~60s（全量重建） | **~1s**（磁盘加载） |
| **增量添加** | 全量重建 | **~132ms**（仅编新节点） |
| **中文支持** | 一般 | **优秀**（trigram FTS5） |
| **检索精度** | 语义近似 | **语义 + 子串 + 标签** 混合 |

---

## 🔧 与 AI Agent 集成

### Hermes Agent（MCP 协议）

配置 `~/.hermes/config.yaml`：

```yaml
mcp_servers:
  chainmem:
    command: chainmem
    args: ["serve", "--socket", "/tmp/chainmem.sock"]
```

启动后，Hermes 自动获得三个工具：

| 工具 | 功能 |
|:-----|:-----|
| `chainmem_ingest(text, source, tags)` | 结链：存储记忆 |
| `chainmem_retrieve(query, tags)` | 追溯：检索记忆（支持标签过滤） |
| `chainmem_stats()` | 统计：查看记忆库状态 |

---

## 🗺️ 路线图

```
Phase 1 ✅ 核心闭环
  ├─ 结链（文本 → 切块 → 嵌入 → 存储）
  ├─ 追溯（语义搜索 + 子串匹配 + 指针遍历）
  ├─ MCP 服务器持久化
  ├─ FAISS 索引持久化（秒级启动）
  └─ 增量索引（毫秒级添加）

Phase 2 🏗️ 类人记忆机制
  ├─ 衰减曲线（Forgetting Curve）
  ├─ 联想增强（检索A时推荐相关链）
  ├─ 自动结链（对话中自动记忆）
  └─ 分支消歧（相同前缀不同下文）

Phase 3 🎯 真正的"人脑记忆"
  ├─ 分层记忆（工作记忆 + 短期 + 长期）
  ├─ 记忆整合（多条记忆→知识归纳）
  ├─ 跨 Agent 记忆共享
  └─ 睡眠压缩（像人脑睡觉时整理记忆）
```

---

## 🤝 如何贡献

我们欢迎所有形式的贡献！详细指南请参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

**新手友好任务：**
- 📖 完善文档和示例
- 🐛 修复 Bug
- ✅ 增加测试覆盖
- 🌍 国际化（i18n）
- 💡 提出新功能建议

---

## 📦 项目结构

```
chainmem/
├── pyproject.toml           # 项目配置
├── README.md                # 本文件
├── LICENSE                  # MIT License
├── CONTRIBUTING.md          # 贡献指南
├── src/chainmem/
│   ├── __init__.py          # ChainMemory 主入口
│   ├── core/node.py         # 数据模型（ChainNode, Chain）
│   ├── store/sqlite_store.py # SQLite 持久化
│   ├── pipeline/
│   │   ├── ingester.py      # 结链（切块 → 嵌入 → 串联）
│   │   └── retriever.py     # 追溯（FAISS + 子串 + 指针）
│   └── cli/app.py           # Typer CLI（含 MCP server）
├── scripts/
│   ├── chainmem_server.py   # 持久化 MCP 服务
│   └── benchmark.py         # 性能基准测试
└── tests/
    └── test_core.py         # 核心测试
```

---

## 📄 License

[MIT License](LICENSE) © 2025 yabolee-kkk

---

<p align="center">
  <b>ChainMem — 让 AI 的记忆，像人一样。</b><br>
  <a href="https://github.com/yabolee-kkk/chainmem">GitHub</a> ·
  <a href="https://github.com/yabolee-kkk/chainmem/issues">报告问题</a> ·
  <a href="https://github.com/yabolee-kkk/chainmem/discussions">讨论区</a>
</p>
