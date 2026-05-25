---
title: ChainMem（链忆）— 让 AI Agent 拥有像人一样的联想式回忆
author: yabolee-kkk
tags: [AI, 记忆系统, 开源, RAG, Python, LLM]
date: 2026-05-22
---

# 🧵 ChainMem（链忆）— 让 AI Agent 拥有像人一样的联想式回忆

> **给它一个"线头"，它还你一件"毛衣"。**

## 一、一个困扰我很久的问题

你有没有这样的经历？

你用 AI Agent 聊了一整个下午，把需求、方案、代码都讨论清楚了。第二天打开新会话，它一句"你好，请问有什么可以帮你的？"——**全忘了**。

这就是当下 AI Agent 最尴尬的短板：**没有记忆**。

当然，现在已经有向量数据库（Pinecone、Chroma、Milvus）来做记忆存储。但你试试搜"上次关于股票投资的想法"——返回 10 个孤立片段的，凑不出"当时到底说了什么"。就像图书馆查资料，搜得到碎片，**但拼不回一本书**。

所以我自己写了一个：**ChainMem（链忆）**。

## 二、人的记忆 vs AI 的"记忆"

先想想人的记忆是什么样：

> 你听到一首歌的前奏 → 想起整首歌 → 想起那个夏天 → 想起那个人...

人的记忆是**链式的**——一个线索能引出整段完整的记忆。你不需要"搜索"过去，你只需要一个**线头**，轻轻一扯，整件"毛衣"就出来了。

而现有的 AI 记忆系统本质上是**图书馆模式**：

```
你搜"股票" → 返回 10 个孤立片段：
  [片段A] "...股票..."
  [片段B] "...股票投资..."
  [片段C] "...关于股票..."
  ...
  但凑不出"当时到底说了什么"
```

每条记忆是独立的**点**——能搜到碎片，但**凑不出一段完整的、前后连贯的记忆**。

ChainMem 用**链式结构**模拟人脑的联想回忆：

```
你搜"股票" → 找到链头 → 沿指针遍历 → 整段对话一字不差重现：
  "其实我的想法是股票投资..."
  "应该分散风险"
  "所以我选了消费、科技、医疗三个板块..."
  "每个板块配比大约是..."
```

**不是搜索碎片，而是还原整段记忆。**

## 三、核心设计：链式 + 向量混合架构

ChainMem 的核心架构只有两层，却巧妙的解决了"检索精度"和"上下文完整性"之间的矛盾。

### 3.1 结链（Ingestion）

当你把一段文本存入 ChainMem 时，它做了三件事：

```
原始文本 → ① 语义切块 → ② 向量嵌入 → ③ 链式串联
```

**① 语义切块：** 按标点将文本切成 6~18 字的短语块。太短的块（≤5 字）会自动合并到前一块，因为 sentence-transformers 对超短文本的编码质量很差（不同短文本可能产生完全相同的向量）。

**② 向量嵌入：** 每个块用 sentence-transformers 编码为 384 维的向量，用于后续语义搜索。

**③ 链式串联：** 用双向指针（prev_id / next_id）把所有块串成一条链。这样，找到任意一个节点，就能沿着指针恢复整条链——完整无遗。

### 3.2 追溯（Retrieval）

当你输入查询时，ChainMem 走了三条路径：

```
  用户查询
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
│  next_id     │      │      → Node_C    │
│  忠实还原     │     │  → 完整对话输出    │
└─────────────┘     └──────────────────┘
```

**为什么需要三条路径？**

- **纯语义搜索不够准**：384 维向量中，语义接近但不同内容的文本可能撞到同一区域，准确率仅 85~90%
- **子串匹配精但不广**：能把查询文本"钉"在包含它的节点上，但查不到同义词或意译
- **标签分类辅助排优**：限定搜索域，排除无关领域

三者结合，实测检索准确率达到 **100%**。

### 3.3 关键优化

在生产环境中还踩了几个坑，分享一下：

**坑 1：短文本退化**

sentence-transformers 对 ≤5 字的短文本会产生完全相同的嵌入向量。修复：结链时自动合并短块。

**坑 2：短查询失效**

≤3 字的查询嵌入质量极差，容易被高维空间的无关节点吸走。修复：查询 ≤3 字时自动补齐 `query = query + " " + query`。

**坑 3：FAISS 索引重建太慢**

全量重建 13,000 个节点的索引要 50~60 秒。优化：索引持久化到磁盘（启动 1 秒加载），增量添加只编新节点（130ms）。

## 四、性能对比

| 指标 | 传统向量搜索 | ChainMem | 提升 |
|:-----|:----------:|:--------:|:---:|
| **检索速度** | ~50ms | **~22ms** | 2.3x |
| **结果完整性** | 碎片 | **100% 原始记忆** | ∞ |
| **启动时间** | ~60s（全量重建） | **~1s**（磁盘加载） | 60x |
| **增量添加** | 全量重建 | **~132ms** | 455x |
| **中文支持** | 一般 | **优秀**（trigram FTS5） | — |
| **检索精度** | 语义近似 | **语义+子串+标签** 混合 | 更高 |

测试环境：单核 CPU + 7.5GB 内存，13,000 节点数据库。

## 五、快速开始

### 安装

ChainMem 采用**分层依赖**设计，你可以按需选择安装方式：

| 安装方式 | 命令 | 大小 | 功能 |
|:---------|:-----|:----:|:-----|
| 🪶 **核心版** | `pip install chainmem` | **~2 MB** | CLI、SQLite存储、文本检索 |
| 🔥 **完整版** | `pip install chainmem[full]` | ~1.5~2 GB | 以上 + 语义搜索 |
| 🐍 **源码** | `git clone ... && pip install -e .` | ~20 KB | 自装依赖 |

> **💡 建议：** 先装核心版感受一下，需要语义搜索时再装完整版。

#### 🪶 核心版（推荐首次尝试）

```bash
pip install chainmem
```

几秒下完，支持：
- CLI 全功能（ingest / retrieve / stats / serve）
- SQLite 持久化 + FTS5 文本搜索
- MCP 服务器
- **语义搜索需额外安装依赖**

#### 🔥 完整版（启用语义搜索）

```bash
pip install chainmem[full]
```

自动安装 sentence-transformers 和 faiss-cpu，带来 FAISS 向量语义搜索和高精度混合检索。

#### 📦 手动安装依赖

如果网络慢或想自己控制版本：

```bash
pip install chainmem
pip install sentence-transformers>=3.0
pip install faiss-cpu>=1.8
```

依赖下载地址（可离线安装）：

| 依赖 | PyPI 地址 | 大小 |
|:-----|:----------|:----:|
| sentence-transformers | pypi.org/project/sentence-transformers/ | ~500 MB |
| faiss-cpu | pypi.org/project/faiss-cpu/ | ~30 MB |
| transformers | pypi.org/project/transformers/ | ~300 MB |
| torch | pypi.org/project/torch/ | ~800 MB |

> **注意：** 只装核心版时，调用 `ingest()` 或 `retrieve()` 会提示安装完整版。CLI 基本操作和 MCP 服务器不受影响。

### Python SDK

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
chainmem ingest "其实我的想法是把每一次的记忆..." --source demo --tags 讨论,记忆系统

# 追溯
chainmem retrieve "其实我的想法"

# 统计
chainmem stats

# 启动 MCP 服务器
chainmem serve --socket /tmp/chainmem.sock
```

## 六、与 AI Agent 集成

ChainMem 原生支持 **MCP（Model Context Protocol）**，可以直接作为 AI Agent 的记忆插件。

### Hermes Agent 配置

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  chainmem:
    command: chainmem
    args: ["serve", "--socket", "/tmp/chainmem.sock"]
```

配置后，Agent 自动获得三个工具：

| 工具 | 功能 |
|:-----|:-----|
| `chainmem_ingest(text, source, tags)` | 结链：存储重要对话 |
| `chainmem_retrieve(query, tags)` | 追溯：检索完整记忆链 |
| `chainmem_stats()` | 统计：查看记忆库状态 |

### 实际使用场景

**场景 1：跨会话记忆**

用户在新会话中说："还记得上次我们讨论的那个股票策略吗？"

Agent 调用 `chainmem_retrieve(query="股票策略", tags="股决")`，拉出上次的完整讨论，就好像昨天刚聊过一样。

**场景 2：自动记录**

讨论中用户说"这个记下来"，Agent 调用 `chainmem_ingest(tags="架构设计")`，把刚才的讨论存入长期记忆。

**场景 3：知识库查询**

Agent 收到一个问题，先查 ChainMem 看看有没有相关记忆，有则引用完整上下文作答，无则从零推理。

## 七、项目架构

```
chainmem/
├── pyproject.toml           # 项目配置
├── README.md                # 中文文档
├── README.en.md             # English version
├── LICENSE                  # MIT License
├── CONTRIBUTING.md          # 贡献指南
├── assets/                  # Logo + 架构图
├── src/chainmem/
│   ├── __init__.py          # ChainMemory 主入口
│   ├── core/node.py         # 数据模型（ChainNode, Chain）
│   ├── store/sqlite_store.py # SQLite 持久化
│   ├── pipeline/
│   │   ├── ingester.py      # 结链（切块 → 嵌入 → 串联）
│   │   └── retriever.py     # 追溯（FAISS + 子串 + 指针）
│   └── cli/app.py           # Typer CLI + MCP server
├── scripts/
│   ├── chainmem_server.py   # 持久化 MCP 服务
│   └── benchmark.py         # 性能基准测试
└── tests/
    └── test_core.py         # 核心测试
```

## 八、开源与未来

ChainMem 是一个**开源项目**，仓库在 GitHub：[https://github.com/yabolee-kkk/chainmem](https://github.com/yabolee-kkk/chainmem)

当前处于 **Alpha 阶段**（v0.5.3），核心闭环已完成：

- ✅ 结链（文本 → 切块 → 嵌入 → 存储）
- ✅ 追溯（语义搜索 + 子串匹配 + 指针遍历）
- ✅ **双向遍历**（匹配节点上下文 100% 还原）
- ✅ **模糊分词兜底**（短查询、重组查询零召回问题解决）
- ✅ **Levenshtein 拼写纠错**（拼写错误自动纠正）
- ✅ MCP 服务器持久化
- ✅ FAISS 索引持久化（秒级启动）
- ✅ 增量索引（毫秒级添加）
- ✅ 标签分类
- ✅ Python SDK + CLI

### 路线图

```text
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

欢迎任何形式的贡献！无论是修 Bug、加功能、写文档，还是提 Issue 讨论，"链忆"的未来由社区共同构建。

## 写在最后

ChainMem 的灵感来自一个简单的观察：**AI Agent 说一句话就要翻一次向量数据库，但人类翻的是一本线装书——找到线索，顺着线，拉出整段记忆。**

链式结构不是什么新技术——链表是每个程序员学数据结构第一课就接触的东西。但有时候，最简单的数据结构，恰好能解决最复杂的问题。

"回忆"不是搜索碎片。**回忆，是把整件毛衣从头到尾拿出来。**

---

**GitHub:** https://github.com/yabolee-kkk/chainmem  
**PyPI:** https://pypi.org/project/chainmem/  
**安装：**
- 🪶 核心版（秒级下载）：`pip install chainmem`
- 🔥 完整版（含语义搜索）：`pip install chainmem[full]`
- 🔐 加密版（凭证自动加密）：`pip install chainmem[secure]`
- 🚀 全功能版：`pip install chainmem[full,secure]`
- 📦 离线安装：下载 whl 后 `pip install *.whl`
