# 贡献指南

欢迎你参与到 ChainMem 项目中来！🎉

无论是修复 Bug、完善文档、增加测试、还是提出新功能建议，我们都非常欢迎。

---

## 🚀 快速开始

```bash
# Fork 项目
# Clone 到本地
git clone https://github.com/你的用户名/chainmem.git
cd chainmem

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
python -m pytest tests/
```

## 🧪 测试

```bash
# 运行全部测试
python -m pytest tests/

# 运行单个测试文件
python -m pytest tests/test_core.py -v

# 带覆盖率
python -m pytest tests/ --cov=chainmem
```

## 📝 提交规范

我们使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<type>: <简短描述>

<可选详细说明>
```

| Type | 说明 |
|------|------|
| `feat:` | 新功能 |
| `fix:` | Bug 修复 |
| `docs:` | 文档变更 |
| `refactor:` | 重构 |
| `test:` | 测试相关 |
| `perf:` | 性能优化 |
| `chore:` | 构建/工具链变更 |

示例：
```
feat: 添加按标签过滤检索功能

用户现在可以在 retrieve 时指定 tags 参数，
只搜索包含特定标签的记忆链。
```

## 🔧 开发指南

### 项目结构速览

```
src/chainmem/
├── __init__.py           # ChainMemory 主入口
├── core/node.py          # 数据模型
├── store/sqlite_store.py # SQLite 持久化
├── pipeline/
│   ├── ingester.py       # 结链
│   └── retriever.py      # 追溯
└── cli/app.py            # CLI 入口
```

### 添加新功能

1. 先在 [Issues](https://github.com/yabolee-kkk/chainmem/issues) 创建讨论
2. 创建功能分支：`git checkout -b feat/你的功能`
3. 实现 + 写测试
4. 确保所有测试通过
5. 提交 PR

## ✅ PR 清单

提交 PR 前请确认：

- [ ] 代码遵循现有风格
- [ ] 添加了相关测试
- [ ] 所有测试通过
- [ ] 更新了文档（如有必要）
- [ ] Commit 信息符合规范

## 💡 新手友好 Issues

标签为 `good first issue` 或 `help wanted` 的任务特别适合新手参与。
如果你有任何问题，欢迎在 Issue 下留言！

---

**感谢你让 ChainMem 变得更好！** 🧵✨
