# ChainMem 跨 Agent 工作记忆

ChainMem 可以把多个 AI Agent 的工作经历集中保存，并回答：

- 前两天是谁在做 ChainMem 部署？
- 上次修复局域网记忆服务的是哪个 Agent？
- 某个项目最近由哪些 Agent 推进过？
- 这项工作留下了哪些文件、端口或提交？

工作记忆是辅助回忆系统。经过验证的项目结论仍应写入正式知识库。

## 记录内容

每条工作事件包含：

- Agent 身份
- 机器名称
- 会话 ID
- 项目
- 任务摘要
- 工作状态
- 产出物
- 标签
- 发生时间
- 关联的 ChainMem 记忆链 ID
- 可见范围

状态支持：

- `planned`
- `active`
- `blocked`
- `done`
- `failed`
- `unknown`

所有时间使用 UTC ISO 8601。

## CLI 使用

记录工作事件：

```bash
chainmem work record \
  "完成 ChainMem 跨 Agent 工作记忆" \
  --agent-id hermes \
  --project chainmem \
  --status done \
  --artifacts docs/work-memory.md,scripts/deploy.sh \
  --tags 部署,记忆
```

根据线索回忆：

```bash
chainmem work recall \
  "前两天做局域网记忆服务" \
  --days 7 \
  --project chainmem
```

查看时间线：

```bash
chainmem work timeline --days 7
```

读取单条事件：

```bash
chainmem work get <event-id> --agent-id hermes
```

私有事件只有所属 Agent 显式传入自己的身份时才能读取。

## MCP 工具

| 工具 | 用途 |
|:-----|:-----|
| `work_memory_record` | 记录工作事件 |
| `work_memory_recall` | 根据模糊描述回忆 |
| `work_memory_timeline` | 按时间倒序查看近期工作 |
| `work_memory_get` | 读取单条事件 |

回忆结果会明确标记为“工作记忆”，不能直接当作知识库事实。

## Agent Token

默认 HTTP 服务只监听 `127.0.0.1`，不需要 Token。

需要让局域网其他 Agent 使用时，创建 Token 文件：

```bash
mkdir -p "$HOME/.chainmem"
install -m 600 /dev/null "$HOME/.chainmem/agents.tokens"
```

文件格式：

```text
# agent-id:token
hermes:replace-with-random-token
codex:replace-with-another-random-token
```

生成随机 Token：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

规则：

- 每行一个 Agent。
- Token 必须唯一。
- 文件权限必须是 `600` 或更严格。
- Token 不写入日志。
- HTTP 请求使用 `Authorization: Bearer <token>`。
- 启用 Token 后，调用方传入的 `agent_id` 会被服务端身份覆盖。

## 启动 HTTP MCP

仅本机：

```bash
chainmem serve \
  --socket /tmp/chainmem.sock \
  --http-host 127.0.0.1 \
  --http-port 3115
```

局域网：

```bash
chainmem serve \
  --socket /tmp/chainmem.sock \
  --http-host 0.0.0.0 \
  --http-port 3115 \
  --agent-tokens-file "$HOME/.chainmem/agents.tokens"
```

非回环地址没有 Token 文件时，服务会拒绝启动。

## MCP 配置

```yaml
mcp_servers:
  chainmem:
    url: http://192.168.1.31:3115/mcp
    transport: http
    headers:
      Authorization: Bearer replace-with-random-token
```

不同 Agent 使用自己的 Token，不需要在配置中填写 `agent_id`。

## systemd 用户服务

示例：

```ini
[Unit]
Description=ChainMem MCP and Web Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/boronglee/chainmem
Environment=HF_ENDPOINT=https://hf-mirror.com
Environment=HTTPS_PROXY=http://127.0.0.1:10808
Environment=HTTP_PROXY=http://127.0.0.1:10808
ExecStart=/home/boronglee/chainmem/.venv/bin/chainmem serve \
  --socket /tmp/chainmem.sock \
  --http-host 0.0.0.0 \
  --http-port 3115 \
  --agent-tokens-file /home/boronglee/.chainmem/agents.tokens \
  --db /home/boronglee/.chainmem/data.db
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

应用：

```bash
systemctl --user daemon-reload
systemctl --user restart chainmem.service
systemctl --user status chainmem.service
```

## 安全建议

- 只在可信局域网开放服务。
- 每个 Agent 使用独立 Token。
- 不要把 Token 提交到 Git。
- 不要把 Token 发到聊天群。
- 定期轮换 Token。
- 不保存密码、私钥、API Key 或完整环境变量。
- 产出物字段只记录路径、链接、提交号或端口。
- 私有事件仍共享同一个数据库，数据库备份需要同等保护。

## 备份和迁移

迁移前备份：

```bash
systemctl --user stop chainmem.service
cp "$HOME/.chainmem/data.db" "$HOME/.chainmem/data.db.bak.$(date +%Y%m%d%H%M%S)"
cp "$HOME/.chainmem/secret.key" "$HOME/.chainmem/secret.key.bak.$(date +%Y%m%d%H%M%S)"
```

迁移只新增 `work_events` 表，不会修改已有链和节点。

## 回滚

代码回滚：

```bash
git revert <commit>
git push origin main
systemctl --user restart chainmem.service
```

数据回滚：

```bash
systemctl --user stop chainmem.service
cp "$HOME/.chainmem/data.db.bak.YYYYMMDDHHMMSS" "$HOME/.chainmem/data.db"
systemctl --user start chainmem.service
```

## 验证

```bash
curl -sS http://127.0.0.1:3115/health

curl -sS http://127.0.0.1:3115/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

工具列表应包含七个工具。
