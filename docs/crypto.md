# ChainMem 加密凭证存储

ChainMem v0.5.0+ 支持**自动检测凭证 + Fernet 加密存储**。

## 原理

在 `ingest` 时检测文本是否包含敏感信息（API token、密码、私钥等），如果匹配且加密器已配置，自动用 `cryptography.fernet` 加密存储。检索时**透明解密**（有密钥时），无密钥时显示 `[🔒]` 标记。

## 使用

```bash
# 1. 安装加密依赖
pip install chainmem[secure]

# 2. 初始化密钥（生成 ~/.chainmem/secret.key）
chainmem secure init

# 3. 设置环境变量（可选，覆盖文件密钥）
export CHAINMEM_KEY=你的密钥

# 4. 之后所有 ingest 自动检测并加密
chainmem ingest "我的 token 是 pypi-xxxx..."

# 5. 查询时透明解密
chainmem retrieve "token"
# → "我的 token 是 pypi-xxxx..."（自动解密）

# 6. 无密钥时
unset CHAINMEM_KEY
chainmem retrieve "token"
# → "[🔒 加密内容（需配置 CHAINMEM_KEY）]"
```

## CLI 命令

```bash
chainmem secure init      # 生成密钥文件
chainmem secure status    # 查看加密状态
chainmem secure encrypt <node-id>   # 手动加密节点
chainmem secure decrypt <node-id>   # 手动解密节点
```

## 自动检测的凭证类型

- `pypi-` — PyPI API token
- `sk-` — OpenAI / DeepSeek / Anthropic API key
- `ghp_` / `gho_` / `ghu_` / `ghr_` — GitHub tokens
- `AKIA` — AWS access key
- `AIza` — Google API key
- `xoxb-` / `xoxa-` / `xoxp-` / `xoxr-` / `xoxs-` — Slack tokens
- `-----BEGIN ... PRIVATE KEY-----` — PEM 私钥
- `Password=` / `Secret=` / `ApiKey=` — 密码字段
- 长度 ≥ 40 且字母数字占比 ≥ 85% 的长字符串
