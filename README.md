# ChainMem（链忆）

**链式 + 向量混合记忆系统** — 让 AI Agent 想起开头几个字，就能完整复原一段记忆。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 核心理念

当前 AI Agent 的记忆系统（如 agentmemory）基于向量语义检索，每条记忆是独立的"点"——能搜到碎片，但**凑不出一段完整的、前后连贯的记忆**。

ChainMem 解决这个问题：把对话切成短语块，用**双向指针串联成链**。输入/想起开头几个字 → 匹配链头 → 沿指针遍历 → **原始记忆一字不差地重现**。

```
"其实我的想法" 
    → 匹配链头节点 
    → next_id 指针遍历 
    → "其实我的想法是把每一次的记忆...全部变成一个链条"
```

---

## 快速开始

```bash
# 安装
pip install chainmem

# 结链：把一段文本存为记忆链
chainmem ingest "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条" --source demo

# 追溯：输入前缀，拉出整条链
chainmem retrieve "其实我的想法"
# → 完整记忆重现

# 统计
chainmem stats
```

---

## 工作原理

```
结链（Ingest）:
  文本 → 按标点/语义切块 → 嵌入(sentence-transformers) 
       → 创建 ChainNode（双向指针串联）→ SQLite 存储
       
追溯（Retrieve）:
  查询 → FAISS 语义搜索 + 子串匹配加分 
       → 选最佳匹配节点 → next_id 链遍历 → 完整文本输出
```

### 混合检索

| 方法 | 作用 | 场景 |
|:----|:----|:------|
| **FAISS 语义搜索** | 兜大圈，找意思相近 | 用户记不清原话，只记得大概 |
| **子串匹配加分** | 精确命中，找字面相同 | 短查询、嵌入模型盲区 |
| **指针链遍历** | 100% 忠实还原 | 确保输出的是原始记忆，不是"编造的" |

---

## CLI 命令

```bash
# 结链
chainmem ingest <text>               # 把文本存为记忆链
  --source, -s  <name>               # 来源会话
  --tags, -t    <tag1,tag2>          # 标签
  --db, -d      <path>               # 数据库路径

# 追溯
chainmem retrieve <query>            # 查询并还原完整记忆
  --max-steps   <N>                  # 最大遍历步数
  --db, -d      <path>

# 统计
chainmem stats                       # 查看所有链/节点统计

# 演示
chainmem demo                        # 快速功能演示

# MCP 服务器（用于 Hermes Agent 集成）
chainmem mcp                         # 启动 MCP 协议服务器
```

---

## Python SDK

```python
from chainmem import ChainMemory

# 初始化
cm = ChainMemory(db_path="~/.chainmem/data.db").open()

# 结链
chain = cm.ingest(
    "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条",
    source="demo",
    tags=["讨论", "记忆系统"],
)
print(f"链 ID: {chain.id}, 节点数: {chain.node_count}")

# 追溯
result = cm.retrieve("其实我的想法")
print("".join(result))  # → 完整记忆

# 切换嵌入模型
cm.set_model("intfloat/multilingual-e5-small")

# 统计
print(cm.stats())

cm.close()
```

---

## 嵌入模型

| 模型 | 大小 | 维度 | 纯语义准确率 | 推荐场景 |
|:----|:---:|:----:|:-----------:|:--------|
| all-MiniLM-L6-v2 | 80MB | 384 | 85% | 默认（轻量快速） |
| multilingual-e5-small | 470MB | 384 | 90% | 多语言更好 |

> 注：以上为纯 FAISS 语义检索准确率。配合子串混合检索后，两者均可达 **100%**。

---

## 项目结构

```
chainmem/
├── pyproject.toml           # 项目配置
├── README.md                # 本文件
├── LICENSE                  # MIT License
├── src/chainmem/
│   ├── __init__.py          # ChainMemory 主入口
│   ├── core/node.py         # 数据模型
│   ├── store/sqlite_store.py # SQLite 持久化
│   ├── pipeline/
│   │   ├── ingester.py      # 结链
│   │   └── retriever.py     # 追溯（FAISS + 子串 + 指针）
│   └── cli/app.py           # Typer CLI（含 MCP server）
├── tests/
│   └── test_core.py         # 16 个测试
└── scripts/
    ├── benchmark.py          # 标准基准测试
    ├── benchmark_compare.py  # 多模型对比
    ├── benchmark_semantic.py # 纯语义对比
    └── chainmem_server.py   # 持久化 MCP 服务
```

---

## 开发

```bash
git clone https://github.com/ZhuLinsen/chainmem.git
cd chainmem
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m pytest tests/
```

---

## 与 Hermes Agent 集成

ChainMem 通过 MCP 协议与 Hermes Agent 集成。有两种模式：

### 按需模式（默认）
```yaml
mcp_servers:
  chainmem:
    command: chainmem
    args: ["mcp"]
```

### 持久化模式（推荐，毫秒级响应）
先启动常驻服务：
```bash
chainmem serve --socket /tmp/chainmem.sock
```

配置 Hermes 通过 socat 连接：
```yaml
mcp_servers:
  chainmem:
    command: socat
    args: ["-", "UNIX-CONNECT:/tmp/chainmem.sock"]
    timeout_seconds: 120
```

集成后，Hermes Agent 可调用以下工具：
- `chainmem_ingest(text, source, tags)` — 结链
- `chainmem_retrieve(query, tags)` — 追溯（支持标签过滤）
- `chainmem_stats()` — 统计

---

## 路线图

- [x] Phase 1: 核心链式记忆（结链 + 存储 + 追溯）
- [x] 混合检索（FAISS + 子串匹配）
- [x] 可配置嵌入模型
- [x] 标签分类（按项目/类型组织记忆）
- [x] 持久化 MCP 服务（systemd 管理，毫秒级响应）
- [ ] Phase 2: 衰减/压缩（Forgetting Curve）
- [ ] Phase 2: 转移矩阵迭代（Modern Hopfield）
- [ ] Phase 2: 分支消歧
- [ ] GitHub Actions CI
- [ ] PyPI 发布

---

## License

MIT License — 详见 [LICENSE](LICENSE)
