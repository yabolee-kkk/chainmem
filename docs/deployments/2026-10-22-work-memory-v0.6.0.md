# ChainMem v0.6.0 工作记忆部署记录

**部署日期：** 2026-10-22

**部署机器：** `192.168.1.31`（`boronglee`）

**版本：** ChainMem `0.6.0`

**Git 标签：** `v0.6.0`

**主分支提交：** `a07376d feat: add cross-agent work memory`

## 部署内容

- 合并 `feature/cross-agent-memory` 到 `main`
- 安装 editable 包 `0.6.0`
- 启用跨 Agent 工作事件存储
- 启用 `work_memory_record`
- 启用 `work_memory_recall`
- 启用 `work_memory_timeline`
- 启用 `work_memory_get`
- 启用 HTTP Agent Token 认证
- HTTP 监听地址切换为 `0.0.0.0:3115`
- Unix Socket 保持 `/tmp/chainmem.sock`

## 生产路径

- 项目：`/home/boronglee/chainmem`
- 虚拟环境：`/home/boronglee/chainmem/.venv`
- 数据库：`/home/boronglee/.chainmem/data.db`
- 加密密钥：`/home/boronglee/.chainmem/secret.key`
- Agent Token：`/home/boronglee/.chainmem/agents.tokens`
- systemd 单元：`/home/boronglee/.config/systemd/user/chainmem.service`

Token 文件权限为 `600`。systemd 服务使用 `UMask=0077`，数据库、WAL、
密钥和 Token 文件均保持 `600`。当前配置的 Agent 标识：

- `claude`
- `cc-connect`
- `codex`
- `hermes`
- `openclaw`

Token 值未写入本文档或 Git。

## 备份

备份时间：`20261022-113156`

- `/home/boronglee/.chainmem/data.db.bak.20261022-113156`
- `/home/boronglee/.chainmem/secret.key.bak.20261022-113156`
- `/home/boronglee/.config/systemd/user/chainmem.service.bak.20261022-113156`

如果存在 WAL/SHM 文件，也已在同一时间点备份。

## 验证结果

- 完整自动化测试：`33 passed`
- systemd：`enabled`
- 服务状态：`active`
- HTTP 监听：`0.0.0.0:3115`
- 本机健康检查：HTTP 200
- 局域网健康检查：HTTP 200
- Web Dashboard：HTTP 200
- 未认证 MCP 请求：HTTP 401
- 已认证 HTTP MCP：七个工具
- Unix Socket MCP：七个工具
- Token 身份防伪：通过
- 真实部署事件写入：通过
- 跨 Agent 语义回忆：通过

第一条生产工作记忆记录为：

```text
部署 ChainMem v0.6.0 跨 Agent 工作记忆
```

该记录由 `codex` 身份写入，并通过 `hermes` 身份成功回忆。

## Pi Agent 接入

- Pi 版本：见 `pi --version`
- 配置文件：`/home/boronglee/.pi/agent/mcp.json`
- 配置备份：`/home/boronglee/.pi/agent/mcp.json.bak.chainmem.20261022-115829`
- MCP 名称：`chainmem`
- MCP 地址：`http://127.0.0.1:3115/mcp`
- 传输方式：Streamable HTTP
- 工具暴露模式：`direct`
- 连接状态：`connected`
- 配置权限：`600`
- Pi 独立身份：`pi`

`pi mcp list --json` 显示 ChainMem 的七个工具全部可用：

- `chainmem_ingest`
- `chainmem_retrieve`
- `chainmem_stats`
- `work_memory_record`
- `work_memory_recall`
- `work_memory_timeline`
- `work_memory_get`

已通过 `pi` 身份写入工作事件：

```text
Pi Agent 接入 ChainMem MCP
```

并通过 `codex` 身份成功回忆，确认 Pi Token 映射和跨 Agent 回忆均正常。

## Codex CLI 接入

- Codex CLI：`0.163.0`
- 配置文件：`/home/borong/.codex/config.toml`
- 配置备份：`/home/borong/.codex/config.toml.bak.chainmem.20261022-134955`
- MCP 地址：`http://192.168.1.31:3115/mcp`
- 认证 helper：`/home/borong/.codex/bin/chainmem-mcp-headers`
- 本机 Token 文件：`/home/borong/.chainmem/codex.token`
- helper 权限：`700`
- Token 权限：`600`
- 只读工具审批：`approve`
- 写入工具：保留默认审批

Codex MCP 客户端实测完成握手并调用 `chainmem_stats`，返回：

```text
链数：2
节点数：11
数据库路径：/home/boronglee/.chainmem/data.db
```

接入过程中修复了两处 Streamable HTTP 兼容问题：

- `notifications/initialized` 返回 `202 Accepted` 且无响应体
- 单请求连接明确发送 `Connection: close`

对应提交：

- `a95c353 fix: return 202 for MCP initialized notifications`
- `0fa09fd fix: close MCP HTTP connections explicitly`
- `11165d4 fix: complete Codex MCP HTTP compatibility`

## 回滚步骤

### 恢复服务和数据库

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user stop chainmem.service
cp /home/boronglee/.chainmem/data.db.bak.20261022-113156 \
  /home/boronglee/.chainmem/data.db
cp /home/boronglee/.chainmem/secret.key.bak.20261022-113156 \
  /home/boronglee/.chainmem/secret.key
cp /home/boronglee/.config/systemd/user/chainmem.service.bak.20261022-113156 \
  /home/boronglee/.config/systemd/user/chainmem.service
systemctl --user daemon-reload
systemctl --user start chainmem.service
```

### 恢复代码

优先使用 Git revert：

```bash
cd /home/boronglee/chainmem
git revert <commit>
git push origin main
```

也可以临时切回上一个已验证版本后重新安装：

```bash
git checkout 2b93709
/home/boronglee/chainmem/.venv/bin/python -m pip install -e .
systemctl --user restart chainmem.service
```

回滚验证：

```bash
curl -sS http://127.0.0.1:3115/health
systemctl --user is-active chainmem.service
```

## 安全事项

- Agent Token 文件不能提交到 Git。
- 本次部署期间使用过的 GitHub Token 曾出现在聊天或群聊中，应单独撤销和轮换。
- 工作记忆是辅助回忆层，不是正式知识库事实来源。
