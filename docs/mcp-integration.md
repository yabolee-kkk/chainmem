# ChainMem MCP 集成指南

ChainMem 通过 MCP（Model Context Protocol）协议与 Hermes Agent 等 AI Agent 集成，提供三个记忆工具。

## 架构

```
Hermes Agent (子进程) ──stdio──→ chainmem_mcp_bridge.py ──Unix socket──→ chainmem_server.py (持久化服务)
```

- **chainmem_server.py** — systemd 管理，开机启动，常驻内存
- **chainmem_mcp_bridge.py** — 每个 Hermes 会话启动一个实例，连接 server 的 Unix socket

## 配置

### 前置条件

1. ChainMem 服务已安装并运行：

```bash
sudo systemctl is-active chainmem
# → active
```

### Hermes 配置

在 `~/.hermes/config.yaml` 的 `mcp_servers` 部分添加：

```yaml
mcp_servers:
  chainmem:
    command: /home/<用户名>/chainmem/scripts/chainmem_mcp_bridge.py
    timeout_seconds: 120
```

### 生效

```bash
# 重启 gateway
sudo systemctl restart hermes-gateway-xxx

# 或在会话内
/reload-mcp
```

## 可用工具

| 工具 | 功能 | 参数 |
|:-----|:-----|:-----|
| `chainmem_ingest` | 结链存储 | text (必填), source, tags |
| `chainmem_retrieve` | 追溯检索 | query (必填), tags |
| `chainmem_stats` | 记忆统计 | 无 |

## 故障排查

### 症状：工具未出现在列表中

1. 确认服务运行：

```bash
sudo systemctl is-active chainmem
ls -la /tmp/chainmem.sock
```

2. 测试桥接脚本：

```bash
# 模拟 MCP 握手
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"hermes","version":"1.0"}}}\n{"jsonrpc":"2.0","id":2,"method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}\n' | python3 /home/<用户名>/chainmem/scripts/chainmem_mcp_bridge.py

# 应输出包含 3 个工具的 JSON
```

3. 如果测试失败，检查 `mcp_servers` 配置中的路径是否正确。

### 常见问题

- **socket 文件不存在** → chainmem 服务未启动：`sudo systemctl start chainmem`
- **桥接脚本连接拒绝** → socket 权限问题：`sudo chmod 666 /tmp/chainmem.sock`
- **工具能注册但调用超时** → 服务卡死：`sudo systemctl restart chainmem`

## 为什么不用 socat

原方案使用 `socat - UNIX-CONNECT:/tmp/chainmem.sock` 作为桥接，但存在两个问题：

1. **行缓冲不可控** — socat 的字节级转发可能在 JSON 消息半截中断
2. **无重试机制** — socket 未就绪时 socat 直接退出，Hermes 收不到 `tools/list` 响应

专用桥接脚本解决了这些问题：
- 使用 `readline()` / `recv(1)` 确保完整行读取
- 连接失败时自动重试 5 次（每次 0.5s）
- 忽略 `notifications/initialized` 避免误响应
- 错误日志输出到 stderr，不污染 MCP 协议数据
