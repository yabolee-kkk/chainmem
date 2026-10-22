# ChainMem 跨 Agent 工作记忆设计方案

**日期：** 2026 年 10 月 22 日

**状态：** 待实施评审

**范围：** 为接入 ChainMem 服务的 AI Agent 提供第一版可用于生产环境的共享工作记忆。

## 目标

允许局域网内任何已授权的 AI Agent 回答下面这类问题：

> 前两天是哪个 Agent 在做 ChainMem 接入？当时产出了什么？

服务会把精简的工作事件记录到 ChainMem，并可选地关联原始对话链；同时提供语义回忆和结构化时间线查询。

## 非目标

- ChainMem 不替代正式知识库。
- 本版本不把整个知识库都建立索引。
- 本版本不会自动把结论写入知识库。
- 本版本默认不保存每一条消息或每一个 token。
- 本版本不会把没有认证的 MCP 服务直接暴露到局域网或互联网。

## 整体架构

```text
AI Agent
  |
  | 通过 HTTP MCP 或本机可信 Unix Socket 调用
  v
ChainMem 工作记忆服务
  |
  +-- work_events 表：结构化工作归属信息
  +-- chains/nodes：可选的原始对话与语义检索
  |
  v
现有知识库继续作为长期、已验证结论的唯一来源。
```

每条工作事件保存一份简洁的任务摘要。如果存在原始对话，则将其保存为 ChainMem 记忆链，并通过 `raw_chain_id` 建立关联。

- 结构化字段回答：谁、什么时候、在哪台机器、当前状态是什么。
- 语义检索回答：用户记得的那项工作具体是什么。

## 各记忆系统的职责

- 原始经历和 Agent 归属：ChainMem。
- 稳定的用户偏好和 Agent 规则：`MEMORY.md`。
- 已验证的项目事实和长期知识：`/data/knowledge_base`。
- 当前任务上下文：当前 Agent 会话。

ChainMem 返回的是回忆线索，不是经过验证的项目事实。Agent 在回答时必须区分这两种信息。

## 数据模型

通过可重复执行的 SQLite 迁移新增 `work_events` 表。

```sql
CREATE TABLE IF NOT EXISTS work_events (
    id             TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    host           TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    project        TEXT NOT NULL DEFAULT '',
    task_summary   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'unknown',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    tags_json      TEXT NOT NULL DEFAULT '[]',
    raw_chain_id   TEXT DEFAULT NULL,
    source         TEXT NOT NULL DEFAULT '',
    started_at     TEXT DEFAULT NULL,
    ended_at       TEXT DEFAULT NULL,
    occurred_at    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    content_hash   TEXT NOT NULL,
    visibility     TEXT NOT NULL DEFAULT 'shared'
);
```

需要建立以下索引：

- `(occurred_at DESC)`
- `(agent_id, occurred_at DESC)`
- `(project, occurred_at DESC)`
- `(session_id)`
- `(raw_chain_id)`
- `UNIQUE(agent_id, session_id, content_hash)`

`status` 支持以下值：

- `planned`：已计划
- `active`：进行中
- `blocked`：受阻
- `done`：已完成
- `failed`：失败
- `unknown`：未知

传入无法识别的状态时，统一保存为 `unknown`。

`visibility` 支持以下值：

- `shared`：共享，默认值
- `private`：私有

私有事件只对所属 Agent 可见。可信的本地运维人员只有显式传入所属 Agent ID 时，才能查询对应的私有事件。

所有时间都以 UTC ISO 8601 格式保存。用户说“前两天”这类相对时间时，由调用的 Agent 先转换成明确的时间范围，再传给服务。

## 结构化 API

新增 `src/chainmem/work_memory.py`，集中负责参数校验、敏感信息清洗、序列化和检索编排。

```python
@dataclass(frozen=True)
class WorkEvent:
    id: str
    agent_id: str
    host: str
    session_id: str
    project: str
    task_summary: str
    status: str
    artifacts: list[str]
    tags: list[str]
    raw_chain_id: str | None
    source: str
    started_at: str | None
    ended_at: str | None
    occurred_at: str
    created_at: str
    visibility: str
```

在 `ChainMemory` 中增加以下公开方法：

```python
def record_work_event(
    self,
    *,
    agent_id: str,
    task_summary: str,
    host: str = "",
    session_id: str = "",
    project: str = "",
    status: str = "unknown",
    artifacts: list[str] | None = None,
    tags: list[str] | None = None,
    source: str = "",
    occurred_at: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    raw_text: str | None = None,
    visibility: str = "shared",
) -> WorkEvent:
    ...

def recall_work_events(
    self,
    *,
    query: str,
    days: int = 7,
    agent_id: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = 5,
    include_private_for: str | None = None,
) -> list[dict]:
    ...

def list_work_events(
    self,
    *,
    days: int = 7,
    agent_id: str | None = None,
    project: str | None = None,
    status: str | None = None,
    limit: int = 30,
    include_private_for: str | None = None,
) -> list[dict]:
    ...

def get_work_event(
    self,
    event_id: str,
    *,
    include_private_for: str | None = None,
) -> dict | None:
    ...
```

参数限制如下：

- `agent_id`：必填，1 到 64 个字符。
- `project`：0 到 128 个字符。
- `task_summary`：必填，1 到 2000 个字符。
- `session_id`：0 到 128 个字符。
- `artifacts`：最多 50 项，每项最多 500 个字符。
- `tags`：最多 30 项，每项最多 64 个字符。
- `limit`：1 到 50。
- `days`：1 到 365。

## 语义回忆流程

记录工作事件时，始终创建一条可检索的记忆链：

- 如果提供了 `raw_text`，就保存原始对话并建立关联。
- 如果没有原始对话，就把任务摘要和结构化上下文保存为记忆链。
- 记忆链来源使用 `work-memory:<event-id>`。
- 标签中至少包含 `work-memory`、`agent:<agent-id>`、项目和状态。

`recall_work_events` 的执行顺序：

1. 先用 SQL 过滤时间范围、Agent、项目、状态和可见性。
2. 再通过 ChainMem 的语义检索对候选事件排序。
3. 如果嵌入模型不可用，自动退回到 SQLite FTS5 或子串匹配。

最多返回 `limit` 条结果，每条结果包含：

```json
{
  "event_id": "...",
  "agent_id": "hermes",
  "host": "borong",
  "session_id": "...",
  "project": "chainmem",
  "task_summary": "...",
  "status": "done",
  "artifacts": ["docs/work-memory.md"],
  "tags": ["chainmem", "deployment"],
  "occurred_at": "2026-10-20T13:20:00Z",
  "raw_chain_id": "...",
  "score": 0.87
}
```

默认返回文本总量不超过 4000 个字符，避免旧记忆挤占当前任务上下文。

## MCP 工具

保留现有三个工具，并新增四个工作记忆工具。

### `work_memory_record`

用于记录一条工作事件。

输入参数：

- `task_summary`：必填
- `project`
- `session_id`
- `status`
- `source`
- `host`
- `artifacts`：字符串数组
- `tags`：字符串数组
- `occurred_at`
- `started_at`
- `ended_at`
- `raw_text`：可选
- `visibility`

启用 HTTP Agent Token 后，服务端认证得到的 Agent ID 具有最高优先级。调用方传入的 `agent_id` 会被忽略，防止伪造身份。

### `work_memory_recall`

用于根据模糊描述回忆过去的工作。

输入参数：

- `query`：必填
- `days`
- `limit`
- 可选 `agent_id`
- 可选 `project`
- 可选 `status`

输出应说明这些是“回忆到的工作事件”，不是已经验证的知识库事实。

### `work_memory_timeline`

用于按时间线查看近期工作。

输入参数：

- `days`
- `limit`
- 可选 `agent_id`
- 可选 `project`
- 可选 `status`

结果按时间倒序排列，包含 Agent、任务摘要、状态、项目和产出物。

### `work_memory_get`

用于读取单条工作事件的完整信息。

输入参数：

- `event_id`

返回完整结构化事件以及关联的 `raw_chain_id`。

## HTTP 身份认证

新增以下启动参数：

- `--http-host`：默认值为 `127.0.0.1`
- `--agent-tokens-file PATH`：Agent Token 配置文件

Token 文件每行一个 Agent：

```text
agent-id:token
```

空行和以 `#` 开头的注释行会被忽略。Token 文件权限必须是 `600` 或更严格。

运行规则：

- HTTP 只监听回环地址时，可以暂时不配置 Token，兼容现有用法。
- HTTP 监听非回环地址时，必须提供合法的 Token 文件，否则服务拒绝启动。
- 启用 Token 后，`/mcp` 必须携带 `Authorization: Bearer <token>`。
- 服务通过常量时间比较把 Token 映射成 `agent_id`。
- Token 不会出现在日志或 MCP 响应中。
- 现有读写工具继续可用，但工作记忆写入身份必须来自认证映射。
- Unix Socket 只允许本机访问。通过 Socket 记录工作事件时，必须显式提供 `agent_id`。

## 敏感信息清洗

在写入数据库之前，对以下字段执行清洗：

- 任务摘要
- 项目名称
- 标签
- 产出物
- 来源

需要识别并替换：

- GitHub、GitLab、PyPI Token
- OpenAI、DeepSeek、Anthropic API Key
- AWS、Google、Slack Token
- Bearer Token
- Password、Secret、API Key 赋值
- PEM 私钥块

匹配到的敏感值替换为 `[REDACTED]`。调用方只会看到布尔值 `redacted`，不会拿到原始敏感内容。

## 命令行工具

增加以下命令：

```text
chainmem work record ...
chainmem work recall QUERY --days 7 ...
chainmem work timeline --days 7 ...
chainmem work get EVENT_ID ...
```

CLI 和 MCP 使用同一套校验和存储逻辑。CLI 属于本地运维入口，因此必须显式传入 `--agent-id`。

## 错误处理

- 参数不合法时，返回包含字段名称的校验错误。
- HTTP 未认证时，返回 401。
- 事件不符合调用方可见权限时，按“不存在”处理。
- 事件 ID 不存在时，返回 not found。
- 语义模型出错时，退回文本检索，不在日志中输出 Token 或秘密。
- 重复工作事件由唯一约束忽略，并返回已有事件。

## 安全与运维

- 默认仍然只监听回环地址。
- 局域网访问必须显式开启，并强制要求 Agent Token。
- Token 文件禁止提交到 Git。
- 第一次执行生产迁移前，先备份 `~/.chainmem/data.db`。
- 默认只保存工作摘要，原始对话按需开启。
- 不保存凭证、完整环境变量或私钥。
- 回滚流程必须写入正式文档。

## 测试范围

单元测试：

- 数据库迁移可以重复执行，且不会破坏已有链和节点。
- 工作事件参数校验和状态归一化。
- 敏感信息清洗。
- 重复事件处理。
- 按项目、Agent、状态、可见性和时间过滤。
- 时间线排序。
- 关联记忆链后的语义检索。
- 语义检索不可用时的文本降级。

集成测试：

- Agent A 写入工作事件，Agent B 能回忆出该工作属于 Agent A。
- 启用 HTTP Token 后，调用方不能伪造其他 Agent 身份。
- 非回环模式没有 Token 文件时，服务拒绝启动。
- HTTP 和 Unix Socket 都能列出全部七个 MCP 工具。
- 原有 ChainMem 测试继续通过。

验收标准：

```text
假设 Hermes 两天前记录了 ChainMem 部署任务，
当任何已授权 Agent 询问当时做了什么，
那么回忆结果应返回 Hermes、任务摘要、时间、项目和产出物。

假设 HTTP 请求伪造了另一个 agent_id，
当 Token 认证已启用，
那么保存的 agent_id 必须仍然来自 Token 映射。

假设不存在匹配事件，
那么服务必须返回空结果，不能编造记忆。
```

## 实施与上线步骤

1. 提交本设计文档。
2. 实现数据库结构和核心 API。
3. 实现 MCP 工具和身份认证。
4. 增加 CLI 和使用文档。
5. 运行完整测试，并完成两个 Agent 的集成测试。
6. 验证通过后合并到 `main`。
7. 备份生产数据库。
8. 先以仅回环地址模式部署。
9. 配置 Agent Token，再切换到局域网监听。
10. 重启服务，验证 HTTP 和 Unix Socket 的全部 MCP 工具。

## 回滚方案

- 代码回滚：在 `main` 上执行 `git revert <commit>`，推送后重启服务。
- 部署回滚：将 systemd 服务切回上一个提交，然后重启。
- 数据回滚：停止服务，恢复迁移前的数据库备份。新增表本身是附加结构，但恢复备份可以确保完全回到原状态。

## 结论

该方案把 ChainMem 定位为跨 Agent 的“工作经历账本”和联想记忆层，而不是知识库替代品。

通过自动记录工作事件、按 Agent Token 确认身份、提供语义回忆和时间线查询，可以实现：

- 任意授权 Agent 都能帮助用户回忆过去的工作
- 用户不必记住具体是哪一个 Agent 做过某项任务
- 原始经历与正式知识库保持清晰边界
- 所有代码、文档和数据变更都可以通过 Git 和数据库备份回滚
