# ChainMem（链忆）— 链式 + 向量混合记忆系统

> **项目需求说明书 (PRD)**
> 版本：v0.1（草案）

---

## 一、项目背景与目标

### 1.1 痛点

当前 AI Agent 的记忆系统（如 agentmemory）基于**向量语义检索**，存在几个根本性缺陷：

| 痛点 | 表现 | 后果 |
|------|------|------|
| 缺乏物理关联 | 每条记忆是独立的向量"点" | 无法精准还原"当时具体说了什么" |
| 无法流式加载 | 必须一次性把全部相关片段拉进 context | 浪费大量 token |
| 无红线效应 | 无法"想起开头几个字就拉出整段记忆" | 不符合人类联想回忆的认知模式 |
| 时序碎片化 | 跨轮对话的因果链丢失 | Agent 不理解观点的演进过程 |

### 1.2 目标

设计并实现一套**链式 + 向量混合记忆系统**，同时具备：

- **链式记忆（Chain Memory）**：用前缀树 + 链表，实现"线头一扯、整件毛衣出来"，精准还原原始对话
- **向量记忆（Vector Memory）**：语义兜底，模糊匹配时回退到向量检索
- **流式加载（Streaming）**：边推理边拉取后续记忆，节省 context token
- **记忆增强/衰减（Forgetting Curve）**：常回想的记忆增强，久不用的记忆压缩

### 1.3 用户故事

```
作为 AI Agent，
我希望通过输入/想起几个字就能拉出整段完整记忆，
以便实现类人的"联想式回忆"。

作为 AI Agent，
我希望模糊搜索也能找到相关内容（当我不记得原话时），
以便在链式检索失效时仍有兜底方案。

作为开发者，
我希望在对话结束后自动"结链"，
以便下一次对话时能快速追溯。

作为系统管理员，
我希望冷数据能自动压缩以减少存储占用，
以便长期运行后不会无限膨胀。
```

---

## 二、核心概念

### 2.1 链节点（ChainNode）

记忆的最小单元——一个语义完整的文本片段。

```
ChainNode {
  id:          UUID         # 唯一标识
  content:     str          # 文本片段（8~20 字为宜）
  prefix:      str          # 前 N 个字（默认 N=3，用于 Trie 索引）
  chain_id:    UUID         # 所属链
  seq:         int          # 链内序号（从 1 开始）
  prev_id:     UUID | None  # 前一个节点
  next_id:     UUID | None  # 后一个节点
  created_at:  datetime
  access_count: int         # 回溯次数
  freshness:   float        # 0.0~1.0，衰减因子
}
```

### 2.2 链（ChainMeta）

整条记忆链的元信息。

```
ChainMeta {
  chain_id:       UUID
  root_id:        UUID        # 首节点 ID
  leaf_id:        UUID        # 末节点 ID
  anchor_prefix:  str         # 整条链的前缀锚点
  length:         int         # 节点数
  summary:        str         # 语义摘要（可选，LLM 生成）
  source_session: str         # 来源会话 ID
  context_tags:   list[str]   # 标签（如 ["股决", "讨论", "架构"]）
  created_at:     datetime
  last_access:    datetime
  strength:       float       # 0.0~1.0，整条链的加强度
}
```

### 2.3 前缀索引（Trie）

高效的快速匹配通道——只存每一条链的**前缀锚点**。

```
Trie 结构示例：
"其实"     → [chain_id_A]
"我只想"   → [chain_id_B]
"关于股"   → [chain_id_C]
```

### 2.4 向量索引（可选层）

语义兜底——当前对话上下文与候选链的语义匹配。

```
embedding(当前上下文) × embedding(链摘要)
→ 相似度排序
→ 选择最匹配的链
```

---

## 三、核心工作流

### 3.1 结链（Ingestion）— 对话→链

```
原始文本
    │
    ▼
Step 1: 语义切块
  - 按自然停顿（句号、问号、逗号、语义转折）切割
  - 每块 8~20 字
    │
    ▼
Step 2: 生成链节点
  - 分配 UUID
  - prev_id / next_id 串联
  - 提取前缀 (content[:3])
    │
    ▼
Step 3: 存储链
  - ChainNode 写入 SQLite
  - ChainMeta 写入 SQLite
  - 前缀注册到 Trie
    │
    ▼
Step 4: 可选—向量嵌入
  - 链摘要生成 embedding
  - 存入向量索引
```

### 3.2 追溯（Retrieval）— 碎片→整链

```
Agent 输入/想起的几个字
    │
    ▼
Step 1: Trie 前缀匹配（O(k)）
  - 命中 → 获得候选链列表
  - 未命中 → 跳到 Step 4
    │
    ▼
Step 2: [多分支消歧]
  - 如果候选链 > 1 条
  - 用当前对话上下文做语义重排
  - 选择最佳链
    │
    ▼
Step 3: 链遍历（流式）
  - 返回 Node_A.content 给 Agent（立即可用）
  - 后台预取 Node_B, Node_C...
  - 边走边取，不阻塞
  - 更新 access_count + freshness
    │
    ▼
Step 4: [向量兜底]
  - 如果 Trie 无命中
  - 回退到向量语义检索
  - 返回相关片段
```

### 3.3 分支处理（Branching）

相同前缀、不同下文时的处理。

```
场景：
  对话 1：Node_A("其实我想") → Node_B1("关于股票...") → ...
  对话 2：Node_A("其实我想") → Node_B2("关于博客...") → ...

方案 A（推荐）：两条独立链，Trie 都命中同一个前缀
  → 向量重排选择最佳
  → 完整链遍历

方案 B：分支指针
  Node_A.next_id = Node_B1
  Node_A.branch_next = [Node_B2, ...]
```

### 3.4 衰减与压缩（Decay & Compaction）

```
每条链有 freshness 分数：
  - 每次被回溯：freshness += Δ（上限 1.0）
  - 定期衰减：freshness *= decay_factor（如 0.95/天）
  - freshness < 阈值（如 0.3）：压缩为摘要节点
  - freshness 回升（被再次命中）：自动解压
```

---

## 四、技术架构

### 4.1 整体架构

```
┌─────────────────────────────────────────────────┐
│                   AI Agent 客户端                  │
│         (Hermes / Claude / 自定义 Agent)            │
└──────────────────────┬──────────────────────────┘
                       │
              ┌────────▼────────┐
              │   ChainMem SDK   │  ← Python 包
              │  (chainmem.*)    │
              └────────┬────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
   │  Trie    │   │  SQLite  │   │ vector   │
   │ (内存)   │   │ (持久化) │   │ (可选)   │
   └─────────┘   └─────────┘   └─────────┘
```

### 4.2 技术选型

| 层 | 技术 | 理由 |
|----|------|------|
| 语言 | Python 3.10+ | 与 Hermes/agentmemory 生态一致 |
| 持久存储 | SQLite3（标准库） | 零依赖，零运维，够用 |
| Trie 索引 | 内存 dict 实现 | 小规模够用，未来可切 Redis |
| 向量嵌入 | sentence-transformers（可选） | 轻量，本地运行 |
| CLI | Typer + Rich | 一致的体验，用户已熟悉 |
| 测试 | pytest | 行业标准 |

### 4.3 项目结构

```
chainmem/
├── pyproject.toml           # 项目元数据 + 依赖
├── README.md
├── PRD.md                   # 本需求文档
│
├── src/
│   └── chainmem/
│       ├── __init__.py
│       ├── core/
│       │   ├── node.py          # ChainNode 数据模型
│       │   ├── chain.py         # ChainMeta + 链操作
│       │   ├── trie.py          # 前缀 Trie 索引
│       │   └── vector.py        # 向量嵌入 + 语义排序
│       │
│       ├── store/
│       │   ├── base.py          # 存储抽象接口
│       │   ├── sqlite_store.py  # SQLite 实现
│       │   └── memory_store.py  # 内存实现（调试/测试用）
│       │
│       ├── pipeline/
│       │   ├── ingester.py      # 结链流程（chunk→link→store）
│       │   ├── retriever.py     # 追溯流程（match→traverse→reconstruct）
│       │   └── decay.py         # 衰减与压缩调度
│       │
│       ├── cli/
│       │   ├── app.py           # Typer CLI 入口
│       │   ├── ingest.py        # 结链命令
│       │   └── retrieve.py      # 追溯命令
│       │
│       └── integration/
│           └── hermes.py        # Hermes Agent 适配器
│
└── tests/
    ├── test_node.py
    ├── test_chain.py
    ├── test_trie.py
    ├── test_ingester.py
    ├── test_retriever.py
    └── test_decay.py
```

---

## 五、API 设计（草案）

### 5.1 Python SDK

```python
from chainmem import ChainMemory

# 初始化
cm = ChainMemory(db_path="~/.chainmem/data.db")

# 结链：将一段文本转化为记忆链
chain = cm.ingest(
    text="其实我的想法是把每一次的记忆包括一次对话全部变成一个链条",
    source_session="session_001",
    tags=["讨论", "记忆系统"]
)
# → ChainMeta(id=..., root_id=..., length=4)

# 追溯：输入前缀或关键词，拉出整条链
result = cm.retrieve(
    prefix="其实我",          # 链式：前缀匹配
    context="当前对话上下文"  # 用于分支消歧
)
# → RetrievedChain(chain=ChainMeta, nodes=[Node_A, Node_B, ...])

# 模糊搜索：向量兜底
result = cm.search("想法变成链条")
# → RetrievedChain 或 None

# 统计
stats = cm.stats()
# → {"total_chains": 42, "total_nodes": 168, "active_chains": 12}
```

### 5.2 CLI

```bash
# 结链
chainmem ingest "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条" \
    --source session_001 \
    --tags 讨论,记忆系统

# 追溯（链式优先）
chainmem retrieve "其实我的" --context "当前对话"

# 搜索（向量兜底）
chainmem search "想法变成链条"

# 查看统计
chainmem stats

# 手动触发衰减压缩
chainmem compact --threshold 0.3

# 查看一条链的详情
chainmem chain show <chain_id>
```

---

## 六、与现有系统的关系

ChainMem **不是替代** agentmemory，而是**补充增强**：

```
agentmemory（现有）     ChainMem（新项目）
├─ 向量语义检索         ├─ 链式优先检索
├─ 4-tier consolidation │ 前缀 Trie + 链表遍历
├─ 知识图谱             ├─ 流式加载
└─ MCP 工具集           └─ 衰减/压缩
          │                     │
          └────────┬────────────┘
                   ▼
         集成到 Hermes Agent
        链式优先 → 未命中回退到向量
```

集成方式：
1. **Phase 1-2**：独立项目，CLI + Python SDK
2. **Phase 3**：Hermes Agent 适配器（`chainmem.integration.hermes`）
3. **Phase 4**：替换/增强 agentmemory 的前端检索定位

---

## 七、验收标准（Success Criteria）

### MVP（Phase 1）完成标准

- [ ] `chainmem.ingest(text)` — 给定一段文本，自动切块、串联、存储到 SQLite
- [ ] `chainmem.retrieve(prefix)` — 给定前缀，从 SQLite 遍历出整条链
- [ ] `chainmem.retrieve` — 前缀不匹配时，返回 None（先不做向量兜底）
- [ ] CLI 命令 `ingest` / `retrieve` / `stats` 可用
- [ ] 测试覆盖率 > 80%

### Phase 2 完成标准

- [ ] Trie 前缀索引（内存，重启重建）
- [ ] 多分支向量重排
- [ ] 衰减/压缩机制
- [ ] `chainmem search` 向量兜底

### Phase 3 完成标准

- [ ] Hermes Agent MCP 工具集成
- [ ] 流式加载（后台预取）

---

## 八、开放问题（Open Questions）

1. **项目名字** — "ChainMem" 可以吗？或者你起一个？
2. **分块策略** — 是按标点符号机械切，还是用 LLM 辅助语义分块？
3. **衰减参数** — 初始衰减因子设为多少？（我建议 0.95/天，阈值 0.3）
4. **向量嵌入** — 用 sentence-transformers（本地轻量）还是复用 agentmemory 的嵌入服务？
5. **存储位置** — 默认放 `~/.chainmem/` 可以吗？
6. **Trie 是否需要持久化** — 还是每次启动从 SQLite 重建？

---

> **下一步：你审完这个需求书，觉得哪里要改/增/删。我们对齐后，进入 Phase 2（技术实现计划）。**
