# ChainMem 跨 Agent 工作记忆实施计划

> **给执行 Agent 的说明：** 按任务顺序执行。每个任务都必须先写失败测试，再写最小实现，运行完整测试，提交并推送 GitHub。不要跳过测试或云端推送。

**目标：** 在 ChainMem 中增加跨 Agent 工作事件记录、语义回忆、时间线和身份认证能力。

**架构：** 使用结构化 `work_events` 表记录归属信息，并关联 ChainMem 记忆链完成语义回忆。HTTP MCP 通过 Agent Token 映射身份，Unix Socket 保持本机可信访问。

**技术栈：** Python 3.10+、SQLite、FAISS、sentence-transformers、Typer、MCP JSON-RPC、pytest。

**设计文档：** `docs/superpowers/specs/2026-10-22-cross-agent-work-memory-design.zh-CN.md`

## 全局约束

- 所有时间使用 UTC ISO 8601。
- 默认监听 `127.0.0.1`。
- 非回环监听必须配置 Agent Token 文件。
- Token 文件权限必须为 `600` 或更严格。
- Token 不得出现在日志、错误信息和 Git 中。
- 每个提交必须包含对应测试或文档。
- 每个提交使用 31 机器已有代理推送到 GitHub。
- 每个任务完成后运行完整测试，不只运行新增测试。
- 工作树：`/home/boronglee/chainmem-worktrees/cross-agent-memory`
- 虚拟环境：`/home/boronglee/chainmem/.venv`
- 测试时设置 `PYTHONPATH=<工作树>/src`，确保导入当前工作树。

---

## 任务 1：工作事件存储、校验和敏感信息清洗

**文件：**

- 创建：`src/chainmem/work_memory.py`
- 修改：`src/chainmem/store/sqlite_store.py`
- 创建：`tests/test_work_memory.py`

**接口：**

- 消费：现有 `SQLiteStore.initialize()` 和 `_migrate_add_column()`。
- 产出：
  - `normalize_work_event(...) -> dict`
  - `redact_secrets(text) -> tuple[str, bool]`
  - `parse_work_event(row) -> dict`
  - `SQLiteStore.save_work_event(event: dict) -> dict`
  - `SQLiteStore.get_work_event(event_id: str) -> dict | None`
  - `SQLiteStore.list_work_events(...) -> list[dict]`

### 步骤 1：写失败测试

创建 `tests/test_work_memory.py`，至少包含以下测试：

```python
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from chainmem.store.sqlite_store import SQLiteStore
from chainmem.work_memory import normalize_work_event, redact_secrets


def test_redact_secrets_masks_common_tokens():
    text, redacted = redact_secrets(
        "token sk-abcdefghijklmnopqrstuvwxyz123456 and password=abcdefgh"
    )
    assert redacted is True
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert "abcdefgh" not in text
    assert "[REDACTED]" in text


def test_normalize_work_event_validates_and_hashes():
    event = normalize_work_event(
        agent_id="hermes",
        task_summary="完成 ChainMem 部署",
        host="borong",
        session_id="session-1",
        project="chainmem",
        status="done",
        artifacts=["docs/work-memory.md"],
        tags=["部署", "记忆"],
        source="feishu",
        occurred_at="2026-10-20T13:20:00Z",
    )
    assert event["status"] == "done"
    assert len(event["content_hash"]) == 64
    assert event["occurred_at"] == "2026-10-20T13:20:00Z"


def test_normalize_work_event_rejects_empty_summary():
    with pytest.raises(ValueError, match="task_summary"):
        normalize_work_event(agent_id="hermes", task_summary="   ")


def test_work_events_table_migrates_existing_database(tmp_path):
    db_path = tmp_path / "data.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE chains (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

    store = SQLiteStore(str(db_path))
    store.initialize()
    columns = {
        row["name"]
        for row in store.conn.execute("PRAGMA table_info(work_events)").fetchall()
    }
    assert {
        "id",
        "agent_id",
        "project",
        "task_summary",
        "occurred_at",
        "content_hash",
        "visibility",
    } <= columns
    store.close()


def test_save_and_get_work_event(tmp_path):
    store = SQLiteStore(str(tmp_path / "data.db"))
    store.initialize()
    event = normalize_work_event(
        agent_id="codex",
        task_summary="实现工作事件表",
        project="chainmem",
        status="active",
        occurred_at="2026-10-22T02:00:00Z",
    )
    saved = store.save_work_event(event)
    loaded = store.get_work_event(saved["id"])
    assert loaded["agent_id"] == "codex"
    assert loaded["artifacts"] == []
    assert loaded["visibility"] == "shared"
    store.close()


def test_list_work_events_filters_days_agent_project_status(tmp_path):
    store = SQLiteStore(str(tmp_path / "data.db"))
    store.initialize()
    store.save_work_event(
        normalize_work_event(
            agent_id="hermes",
            task_summary="ChainMem 部署",
            project="chainmem",
            status="done",
            occurred_at="2026-10-22T01:00:00Z",
        )
    )
    store.save_work_event(
        normalize_work_event(
            agent_id="codex",
            task_summary="博客修复",
            project="blog",
            status="active",
            occurred_at="2026-09-01T01:00:00Z",
        )
    )
    results = store.list_work_events(
        since="2026-10-20T00:00:00Z",
        agent_id="hermes",
        project="chainmem",
        status="done",
    )
    assert [item["task_summary"] for item in results] == ["ChainMem 部署"]
    store.close()
```

### 步骤 2：验证测试失败

```bash
cd "$HOME/chainmem-worktrees/cross-agent-memory"
export PYTHONPATH="$PWD/src"
"$HOME/chainmem/.venv/bin/python" -m pytest \
  tests/test_work_memory.py -q
```

预期：导入 `chainmem.work_memory` 失败，或 `SQLiteStore` 没有工作事件方法。

### 步骤 3：实现最小存储和校验

在 `work_memory.py` 中实现：

- 状态白名单和归一化。
- `visibility` 白名单。
- ISO 8601 UTC 解析和规范化。
- 长度限制校验。
- 内容哈希：`sha256(agent_id + session_id + task_summary + artifacts_json + occurred_at)`。
- `redact_secrets` 覆盖设计文档列出的 Token 和密码模式。
- `normalize_work_event` 返回可直接写入 SQLite 的字典。
- JSON 字段序列化、反序列化和公开结果转换。

在 `SQLiteStore._create_tables()` 中创建 `work_events` 表及索引。迁移必须保留已有 `chains` 和 `nodes` 数据。

`save_work_event` 使用 `INSERT OR IGNORE`。如果唯一约束命中，则查询并返回已有记录。

`list_work_events` 使用参数化 SQL 拼接允许的过滤条件，禁止直接拼接用户输入。

### 步骤 4：验证通过

```bash
cd "$HOME/chainmem-worktrees/cross-agent-memory"
export PYTHONPATH="$PWD/src" HF_ENDPOINT=https://hf-mirror.com \
  HTTPS_PROXY=http://127.0.0.1:10808 \
  HTTP_PROXY=http://127.0.0.1:10808
"$HOME/chainmem/.venv/bin/python" -m pytest -q
```

预期：全部测试通过。

### 步骤 5：提交并推送

```bash
git add src/chainmem/work_memory.py \
  src/chainmem/store/sqlite_store.py tests/test_work_memory.py
git commit -m "feat: add work event storage"
HTTPS_PROXY=http://127.0.0.1:10808 \
HTTP_PROXY=http://127.0.0.1:10808 \
git push origin feature/cross-agent-memory
```

---

## 任务 2：ChainMemory 工作事件 API 和语义回忆

**文件：**

- 修改：`src/chainmem/__init__.py`
- 修改：`src/chainmem/work_memory.py`
- 修改：`src/chainmem/store/sqlite_store.py`
- 修改：`src/chainmem/pipeline/retriever.py`
- 修改：`tests/test_work_memory.py`

**接口：**

- 消费：任务 1 的 `normalize_work_event`、`save_work_event`、`list_work_events`。
- 产出：
  - `ChainMemory.record_work_event(...) -> dict`
  - `ChainMemory.recall_work_events(...) -> list[dict]`
  - `ChainMemory.list_work_events(...) -> list[dict]`
  - `ChainMemory.get_work_event(...) -> dict | None`
  - `Retriever.retrieve_chain_matches(query, tags, limit) -> list[tuple[str, float]]`

### 步骤 1：写失败测试

新增测试：

```python
def test_record_work_event_creates_linked_chain(tmp_path):
    from chainmem import ChainMemory

    cm = ChainMemory(db_path=str(tmp_path / "data.db")).open()
    event = cm.record_work_event(
        agent_id="hermes",
        task_summary="部署 ChainMem 工作记忆",
        project="chainmem",
        status="done",
        tags=["部署"],
        occurred_at="2026-10-22T02:00:00Z",
    )
    assert event["raw_chain_id"]
    chain = cm.store.get_chain(event["raw_chain_id"])
    assert chain["source"] == f"work-memory:{event['id']}"
    cm.close()


def test_recall_work_events_returns_agent_and_filters(tmp_path):
    from chainmem import ChainMemory

    cm = ChainMemory(db_path=str(tmp_path / "data.db")).open()
    cm.record_work_event(
        agent_id="hermes",
        task_summary="完成 ChainMem 局域网部署",
        project="chainmem",
        status="done",
        occurred_at="2026-10-22T02:00:00Z",
    )
    cm.record_work_event(
        agent_id="codex",
        task_summary="修复博客样式",
        project="blog",
        status="active",
        occurred_at="2026-10-22T03:00:00Z",
    )
    results = cm.recall_work_events(
        query="局域网记忆服务",
        days=7,
        project="chainmem",
    )
    assert results
    assert results[0]["agent_id"] == "hermes"
    assert results[0]["task_summary"] == "完成 ChainMem 局域网部署"
    cm.close()


def test_private_event_hidden_from_other_agent(tmp_path):
    from chainmem import ChainMemory

    cm = ChainMemory(db_path=str(tmp_path / "data.db")).open()
    event = cm.record_work_event(
        agent_id="hermes",
        task_summary="私有部署记录",
        visibility="private",
        occurred_at="2026-10-22T02:00:00Z",
    )
    assert cm.get_work_event(
        event["id"], include_private_for="codex"
    ) is None
    assert cm.get_work_event(
        event["id"], include_private_for="hermes"
    ) is not None
    cm.close()
```

### 步骤 2：验证测试失败

运行新增测试，确认 `ChainMemory` 尚无工作记忆方法。

### 步骤 3：实现最小 API

`record_work_event`：

1. 归一化输入。
2. 查询重复事件；存在则直接返回。
3. 使用 `raw_text or task_summary` 创建记忆链。
4. 为记忆链附加标签：`work-memory`、`agent:<id>`、项目和状态。
5. 把链 ID 写入 `raw_chain_id`。
6. 保存结构化事件。

`recall_work_events`：

1. 计算 `since = now_utc - days`。
2. 按结构化条件取候选事件。
3. 调用 `retrieve_chain_matches` 获取相关链和分数。
4. 只保留候选事件中关联的链。
5. 按分数倒序，返回最多 `limit` 条。
6. 如果语义检索异常，使用 `LOWER(task_summary) LIKE` 降级。

`Retriever.retrieve_chain_matches`：

- 复用现有嵌入模型和 `chain_tags`。
- 返回 `[(chain_id, score), ...]`，每个链只保留最高分节点。
- 不遍历链，不返回文本。

### 步骤 4：验证通过

运行完整测试。若语义模型下载导致超时，必须使用已配置的 HuggingFace 镜像和代理。

### 步骤 5：提交并推送

```bash
git add src/chainmem/__init__.py src/chainmem/work_memory.py \
  src/chainmem/store/sqlite_store.py \
  src/chainmem/pipeline/retriever.py tests/test_work_memory.py
git commit -m "feat: recall work events by semantic context"
git push origin feature/cross-agent-memory
```

---

## 任务 3：MCP 工具、Agent Token 和局域网绑定

**文件：**

- 创建：`src/chainmem/auth.py`
- 修改：`src/chainmem/cli/app.py`
- 修改：`tests/test_http_server.py`
- 创建：`tests/test_auth.py`

**接口：**

- 消费：任务 2 的四个 `ChainMemory` 工作事件方法。
- 产出：
  - `load_agent_tokens(path) -> dict[str, str]`
  - `authenticate_token(registry, token) -> str | None`
  - `serve(..., http_host="127.0.0.1", agent_tokens_file=None)`
  - MCP 工具 `work_memory_record`
  - MCP 工具 `work_memory_recall`
  - MCP 工具 `work_memory_timeline`
  - MCP 工具 `work_memory_get`

### 步骤 1：写失败测试

`tests/test_auth.py`：

```python
from __future__ import annotations

import os
import stat

import pytest

from chainmem.auth import load_agent_tokens


def test_load_agent_tokens_requires_private_permissions(tmp_path):
    path = tmp_path / "agents.tokens"
    path.write_text("hermes:token-one\ncodex:token-two\n")
    os.chmod(path, 0o644)
    with pytest.raises(ValueError, match="600"):
        load_agent_tokens(str(path))


def test_load_agent_tokens_parses_comments_and_blank_lines(tmp_path):
    path = tmp_path / "agents.tokens"
    path.write_text("# agents\n\nhermes:token-one\ncodex:token-two\n")
    os.chmod(path, 0o600)
    assert load_agent_tokens(str(path)) == {
        "token-one": "hermes",
        "token-two": "codex",
    }
```

扩展 `tests/test_http_server.py`：

- Token 文件启动服务。
- `tools/list` 返回七个工具。
- 不带 Token 调用 `/mcp` 返回 401。
- 带 Hermes Token 调用 `work_memory_record` 后，`work_memory_recall` 返回 `agent_id=hermes`。
- 请求参数中的伪造 `agent_id` 被忽略。
- 非回环监听没有 Token 文件时服务启动失败。

### 步骤 2：验证测试失败

运行新增测试，确认 `chainmem.auth` 不存在、`/mcp` 未认证、工具数量不足。

### 步骤 3：实现认证

`auth.py`：

- 检查文件权限，拒绝 group/other 任何权限。
- 解析 `agent_id:token`。
- 重复 Agent 或重复 Token 抛出 `ValueError`。
- `authenticate_token` 遍历映射并使用 `hmac.compare_digest`。
- 不记录 Token 内容。

HTTP 请求处理：

- 解析 HTTP Header，不区分 Header 名称大小写。
- 提取 `Authorization: Bearer ...`。
- 认证成功后将 `agent_id` 传给 `_process_mcp_request`。
- 认证失败返回 401 JSON。

服务启动：

- `http_host` 默认为 `127.0.0.1`。
- 非回环地址且没有 Token 文件时，启动前抛出 `typer.BadParameter`。
- HTTP 和 Dashboard 日志显示实际监听地址。

### 步骤 4：实现工作 MCP 工具

把工作工具加入 `tools/list`，并实现 `tools/call` 分支。

`work_memory_record`：

- HTTP 使用认证映射的 Agent ID。
- Unix Socket 必须显式传 `agent_id`。
- 数组参数接受 JSON 数组或逗号分隔字符串。

`work_memory_recall`、`work_memory_timeline`、`work_memory_get`：

- 调用对应 `ChainMemory` 方法。
- 返回 `content: [{"type": "text", "text": ...}]`。
- 返回内容为中文可读文本。

### 步骤 5：验证通过

运行完整测试，并确认七个工具的集合：

```text
chainmem_ingest
chainmem_retrieve
chainmem_stats
work_memory_record
work_memory_recall
work_memory_timeline
work_memory_get
```

### 步骤 6：提交并推送

```bash
git add src/chainmem/auth.py src/chainmem/cli/app.py \
  tests/test_auth.py tests/test_http_server.py
git commit -m "feat: add authenticated work memory MCP tools"
git push origin feature/cross-agent-memory
```

---

## 任务 4：CLI、用户文档和运维说明

**文件：**

- 修改：`src/chainmem/cli/app.py`
- 修改：`README.md`
- 修改：`README.en.md`
- 修改：`docs/mcp-integration.md`
- 创建：`docs/work-memory.md`
- 修改：`pyproject.toml`

**接口：**

- 消费：任务 1 到 3 的公开 API。
- 产出：
  - `chainmem work record`
  - `chainmem work recall`
  - `chainmem work timeline`
  - `chainmem work get`
  - Token 文件示例
  - 局域网部署和回滚说明

### 步骤 1：写 CLI 失败测试

创建 `tests/test_work_cli.py`，使用 Typer `CliRunner` 测试：

- `work record` 输出事件 ID。
- `work recall` 输出 Agent 名称和任务摘要。
- `work timeline` 按时间倒序。
- 缺少 `--agent-id` 时返回非零退出码。

### 步骤 2：验证测试失败

运行 `tests/test_work_cli.py`，确认 `work` 子命令不存在。

### 步骤 3：实现 CLI

新增 `work` Typer 子应用，并注册到主应用。

所有 CLI 命令接受 `--db`。`record` 必须接受 `--agent-id`。

输出使用 Rich 表格或面板，但必须包含：

- 事件 ID
- Agent
- 项目
- 状态
- 时间
- 任务摘要
- 关联记忆链 ID

### 步骤 4：更新文档

`docs/work-memory.md` 必须包含：

- 使用场景
- 数据结构
- Agent Token 文件格式和权限
- systemd 服务配置示例
- 从回环地址切换到局域网监听的步骤
- HTTP 和 Unix Socket 示例
- 安全注意事项
- Git 与数据库回滚步骤

`README.md` 和 `README.en.md` 增加简短入口和链接。

`docs/mcp-integration.md` 增加四个新工具和认证说明。

版本从 `0.5.3` 提升到 `0.6.0`。

### 步骤 5：验证通过

运行完整测试和 CLI 冒烟测试：

```bash
"$HOME/chainmem/.venv/bin/python" -m pytest -q
PYTHONPATH="$PWD/src" \
  "$HOME/chainmem/.venv/bin/python" -m chainmem.cli.app work --help
```

### 步骤 6：提交并推送

```bash
git add src/chainmem/cli/app.py tests/test_work_cli.py \
  README.md README.en.md docs/mcp-integration.md \
  docs/work-memory.md pyproject.toml
git commit -m "feat: add work memory CLI and operations guide"
git push origin feature/cross-agent-memory
```

---

## 任务 5：集成验证、合并和部署

**文件：**

- 修改：`docs/superpowers/plans/2026-10-22-cross-agent-work-memory.md`
- 可能修改：部署文档中的实际命令

### 步骤 1：完整验证

```bash
cd "$HOME/chainmem-worktrees/cross-agent-memory"
export PYTHONPATH="$PWD/src" HF_ENDPOINT=https://hf-mirror.com \
  HTTPS_PROXY=http://127.0.0.1:10808 \
  HTTP_PROXY=http://127.0.0.1:10808
"$HOME/chainmem/.venv/bin/python" -m pytest -q
git diff --check
```

### 步骤 2：两 Agent 集成测试

使用临时数据库和临时 Token 文件启动服务：

1. Hermes Token 写入工作事件。
2. Codex Token 查询并确认返回 Hermes。
3. Codex 尝试伪造 `agent_id=hermes`，确认仍写入 Codex。
4. 无 Token 请求返回 401。
5. Unix Socket 能列出七个工具。

### 步骤 3：检查提交历史

```bash
git log --oneline --decorate main..feature/cross-agent-memory
git status --short
```

确认：

- 每个提交主题清晰。
- 工作区干净。
- 所有提交已推送。

### 步骤 4：合并到 main

```bash
git checkout main
git pull --ff-only origin main
git merge --no-ff feature/cross-agent-memory \
  -m "feat: add cross-agent work memory"
git push origin main
```

### 步骤 5：部署

1. 停止 `chainmem.service`。
2. 备份 `~/.chainmem/data.db` 和 `~/.chainmem/secret.key`。
3. 更新主工作树。
4. 重新安装 editable 包。
5. 先以 `127.0.0.1` 启动，验证七个工具。
6. 创建权限为 `600` 的 Agent Token 文件。
7. 将服务切换为局域网监听地址。
8. 重启服务。
9. 再次执行 HTTP 和 Unix Socket 集成测试。

### 步骤 6：最终验证

必须提供以下新鲜证据：

- 全量 pytest 结果
- Git 分支和提交列表
- HTTP MCP 工具列表
- Unix Socket 工具列表
- 两个不同 Agent 的回忆归属结果
- systemd `enabled` 和 `active`
- `/health` 返回 200

全部通过后，更新本计划文档的任务状态并提交最终部署记录。
