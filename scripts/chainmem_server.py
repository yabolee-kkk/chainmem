#!/usr/bin/env python3
"""ChainMem 持久化 MCP 服务 - 启动包装器

这个脚本不再直接包含服务器逻辑，而是调用 chainmem serve 的完整实现。
PyPI 用户只需：chainmem serve --http-port 3115
"""
import subprocess
import sys

if __name__ == "__main__":
    sys.exit(subprocess.call([
        sys.executable, "-m", "chainmem.cli.app", "serve",
        "--http-port", "3115",
        "--socket", "/tmp/chainmem.sock",
    ]))
