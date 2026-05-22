# ChainMem MCP 集成指南

ChainMem 通过 MCP（Model Context Protocol）协议与 Hermes Agent 等 AI Agent 集成，提供三个记忆工具。

## 架构

```
Hermes Agent (子进程) ──stdio──→ chainmem_mcp_bridge.py ──Unix socket──→ chainmem_server.py (持久化服务)
```

- **chainmem_server.py** — systemd 管理，开机启动，常驻内存
- **chainmem_mcp_bridge.py** — 每个 Hermes 会话启动一个实例，连接 server 的 Unix socket

## 安装 ChainMem

### 最小安装（推荐通过网络不好的环境）

如果服务器网络受限或没有 GPU，优先使用 CPU-only 版：

```bash
# 1. 安装核心版（仅 CLI + SDK，约 22KB）
pip install chainmem

# 2. 安装 CPU-only torch（192MB，不含 CUDA）
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. 安装 sentence-transformers 和 faiss
pip install sentence-transformers faiss-cpu
```

如果 `pip install chainmem[full]` 默认拉 CUDA torch（532MB + 366MB CUDA 组件），网络慢容易超时。

### 设置 HuggingFace 国内镜像（如需）

如果服务器无法直接访问 huggingface.co：

```bash
export HF_ENDPOINT=https://hf-mirror.com
# 然后运行 ChainMem，模型会从国内镜像下载
```

## 配置

### 前置条件

1. ChainMem 服务已安装并运行：

```bash
sudo systemctl is-active chainmem
# → active
```

### 环境变量注意事项

**重要：** Hermes native-mcp 对 stdio 子进程会做环境隔离，只保留 `PATH` / `HOME` 等基础变量。
如果你设置了 `HF_ENDPOINT` 等环境变量，**必须在 config 的 `env` 字段显式声明**，否则子进程拿不到。

### Hermes 配置

在 `~/.hermes/config.yaml` 的 `mcp_servers` 部分添加：

```yaml
mcp_servers:
  chainmem:
    command: /home/<用户名>/chainmem/scripts/chainmem_mcp_bridge.py
    timeout_seconds: 120
    # 环境变量显式声明（重要！否则子进程拿不到）
    env:
      HF_ENDPOINT: "https://hf-mirror.com"
      PATH: "/home/<用户名>/.local/share/pipx/venvs/chainmem/bin:/usr/bin:/bin"
```

如果使用 stdio 模式直接启动（不经过桥接脚本），配置如下：

```yaml
mcp_servers:
  chainmem:
    command: /home/<用户名>/.local/share/pipx/venvs/chainmem/bin/python
    args: ["-m", "chainmem.cli.app", "mcp", "--db", "/home/<用户名>/.chainmem/data.db"]
    timeout_seconds: 120
    env:
      HF_ENDPOINT: "https://hf-mirror.com"
      PATH: "/home/<用户名>/.local/share/pipx/venvs/chainmem/bin:/usr/bin:/bin"
```

### systemd 服务模板

创建 `/etc/systemd/system/chainmem.service`：

```ini
[Unit]
Description=ChainMem MCP Service — persistent chain-memory server
After=network.target

[Service]
Type=simple
User=<你的用户名>
ExecStart=/home/<用户名>/.local/share/pipx/venvs/chainmem/bin/python \
    /home/<用户名>/chainmem/scripts/chainmem_server.py
Restart=on-failure
RestartSec=3

# 环境变量（重要！模型下载用）
Environment=HF_ENDPOINT=https://hf-mirror.com

[Install]
WantedBy=multi-user.target
```

> **注意：** 如果频繁重启触发 systemd rate limit，执行：
> ```bash
> sudo systemctl reset-failed chainmem
> ```

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

3. 如果测试失败，检查 `mcp_servers` 配置中的路径和环境变量是否正确。

### 模型下载失败

```
ConnectionError: ... Failed to connect to huggingface.co ...
```

模型文件无法从 huggingface.co 下载。解决方案：

```bash
# 选项 A：使用国内镜像
export HF_ENDPOINT=https://hf-mirror.com

# 选项 B：预下载到缓存目录
export HF_HOME=~/.cache/huggingface
huggingface-cli download sentence-transformers/all-MiniLM-L6-v2
```

### 常见问题

- **socket 文件不存在** → chainmem 服务未启动：`sudo systemctl start chainmem`
- **桥接脚本连接拒绝** → socket 权限问题：`sudo chmod 666 /tmp/chainmem.sock`
- **工具能注册但调用超时** → 服务卡死：`sudo systemctl restart chainmem`
- **gateway 重启报 rate limit** → 多次快速重启触发 systemd 防护：`sudo systemctl reset-failed hermes-gateway-xxx`
- **环境变量不生效** → Hermes 隔离了子进程环境，请检查 `mcp_servers.<name>.env` 配置

## 为什么不用 socat

原方案使用 `socat - UNIX-CONNECT:/tmp/chainmem.sock` 作为桥接，但存在两个问题：

1. **行缓冲不可控** — socat 的字节级转发可能在 JSON 消息半截中断
2. **无重试机制** — socket 未就绪时 socat 直接退出，Hermes 收不到 `tools/list` 响应

专用桥接脚本解决了这些问题：
- 使用 `readline()` / `recv(1)` 确保完整行读取
- 连接失败时自动重试 5 次（每次 0.5s）
- 忽略 `notifications/initialized` 避免误响应
- 错误日志输出到 stderr，不污染 MCP 协议数据

## 加密存储（v0.5.0+）

详见 [凭证加密文档](crypto.md)。
