"""
ChainMem 加密模块 — 凭证自动检测 + Fernet 对称加密

设计原则：
  1. 可选依赖：cryptography 是 chainmem[secure] 的附加依赖
  2. 透明解密：有密钥时自动解密，无密钥时显示 [🔒 加密内容]
  3. 自动检测：ingest 时正则匹配常见凭证模式，匹配到的节点自动加密

密钥管理：
  - 优先读环境变量 CHAINMEM_KEY
  - 其次读 ~/.chainmem/secret.key
  - 都不存在时返回 None（纯明文模式，仅在初始化时提示一次）
"""

from __future__ import annotations

import os
import re
import base64
import hashlib
from pathlib import Path
from typing import Optional, Tuple

# ── 自动检测的凭证模式 ──────────────────────────────────────────
# 按"误报率"排序 — 高精度模式放前面，降低误标记
CREDENTIAL_PATTERNS: list[re.Pattern] = [
    # 长随机 token（高置信度）
    re.compile(r'(?<![a-zA-Z0-9])pypi-[A-Za-z0-9_-]{60,}(?![a-zA-Z0-9])'),       # PyPI
    re.compile(r'(?<![a-zA-Z0-9])sk-[A-Za-z0-9_-]{32,}(?![a-zA-Z0-9])'),           # OpenAI / DeepSeek / Anthropic
    re.compile(r'(?<![a-zA-Z0-9])ghp_[A-Za-z0-9]{36,}(?![a-zA-Z0-9])'),            # GitHub PAT
    re.compile(r'(?<![a-zA-Z0-9])gho_[A-Za-z0-9]{36,}(?![a-zA-Z0-9])'),            # GitHub OAuth
    re.compile(r'(?<![a-zA-Z0-9])ghu_[A-Za-z0-9]{36,}(?![a-zA-Z0-9])'),            # GitHub user token
    re.compile(r'(?<![a-zA-Z0-9])ghr_[A-Za-z0-9]{36,}(?![a-zA-Z0-9])'),            # GitHub repo token
    re.compile(r'(?<![a-zA-Z0-9])AKIA[A-Z0-9]{16}(?![a-zA-Z0-9])'),                # AWS access key
    re.compile(r'(?<![a-zA-Z0-9])AIza[A-Za-z0-9_-]{35}(?![a-zA-Z0-9])'),           # Google API key
    re.compile(r'(?<![a-zA-Z0-9])xox[baprs]-[A-Za-z0-9-]{24,}(?![a-zA-Z0-9])'),    # Slack tokens
    # PEM 密钥块（高置信度）
    re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'),
    re.compile(r'-----BEGIN\s+EC\s+PRIVATE\s+KEY-----'),
    re.compile(r'-----BEGIN\s+OPENSSH\s+PRIVATE\s+KEY-----'),
    # 密码字段（中等置信度）
    re.compile(r'[Pp]assword\s*[=:]\s*["\']?.{8,}["\']?'),
    re.compile(r'[Ss]ecret\s*[=:]\s*["\']?.{8,}["\']?'),
    re.compile(r'[Aa]pi[Kk]ey\s*[=:]\s*["\']?.{8,}["\']?'),
]

# 密钥文件路径
KEY_DIR = os.path.expanduser("~/.chainmem")
KEY_FILE = os.path.join(KEY_DIR, "secret.key")
ENV_VAR = "CHAINMEM_KEY"


class Encryptor:
    """Fernet 对称加密器 — 加密/解密文本节点"""

    def __init__(self, key: Optional[str] = None):
        """初始化加密器。key 为 None 时尝试从环境变量/密钥文件加载"""
        self._fernet = None
        self._unavailable_reason: str | None = None
        self._init(key)

    def _init(self, key: Optional[str] = None):
        """初始化 Fernet 实例"""
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self._unavailable_reason = (
                "cryptography 未安装。如需加密功能，请：pip install chainmem[secure]"
            )
            return

        resolved = key or os.environ.get(ENV_VAR) or self._load_key_file()

        if resolved is None:
            # 没有密钥 → 纯明文模式
            self._unavailable_reason = (
                "未设置 CHAINMEM_KEY 环境变量，且未找到密钥文件。"
                "运行 chainmem secure init 生成密钥，或设置 CHAINMEM_KEY 环境变量。"
            )
            return

        # 确保密钥为 32 字节 base64 编码的 Fernet 格式
        fernet_key = self._ensure_fernet_key(resolved)
        if fernet_key is None:
            self._unavailable_reason = "密钥格式无效，请重新生成：chainmem secure init"
            return

        self._fernet = Fernet(fernet_key)

    def _load_key_file(self) -> str | None:
        """尝试从 ~/.chainmem/secret.key 读取密钥"""
        path = Path(KEY_FILE)
        if path.exists():
            return path.read_text().strip()
        return None

    @staticmethod
    def _ensure_fernet_key(key: str) -> bytes | None:
        """将用户提供的密钥转换为 Fernet 兼容的 32 字节 base64 密钥"""
        # 如果已经是标准 Fernet 密钥（32字节 base64 url-safe + 等号填充）
        try:
            decoded = base64.urlsafe_b64decode(key)
            if len(decoded) == 32:
                return key.encode("utf-8")
        except Exception:
            pass

        # 否则通过 SHA-256 派生 32 字节
        try:
            derived = hashlib.sha256(key.encode("utf-8")).digest()
            return base64.urlsafe_b64encode(derived)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        """加密器是否可用"""
        return self._fernet is not None

    @property
    def reason(self) -> str | None:
        """加密不可用的原因提示"""
        return self._unavailable_reason

    def encrypt(self, plaintext: str) -> Tuple[str, str]:
        """加密文本 → (ciphertext, iv)

        返回的 ciphertext 可直接存入 SQLite，
        iv 作为 metadata 存储，解密时需提供。
        Fernet 本身将 IV 嵌入密文头部，但我们单独存储 iv
        以支持纯文本 fallback（无 key 时能识别加密节点）。
        """
        if not self._fernet:
            raise RuntimeError(
                "Encryptor not available. " + (self._unavailable_reason or "")
            )
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        # Fernet 格式：version(1) + timestamp(8) + IV(32) + ciphertext + HMAC(32)
        # 提取 IV（第 9-41 字节）
        decoded = base64.urlsafe_b64decode(token)
        iv = base64.urlsafe_b64encode(decoded[9:41]).decode("utf-8")
        return token.decode("utf-8"), iv

    def decrypt(self, ciphertext: str, iv: str | None = None) -> str:
        """解密文本，返回原始明文

        iv 参数保留供自定义解密逻辑使用，Fernet 标准解密无需 iv。
        """
        if not self._fernet:
            raise RuntimeError(
                "Encryptor not available. " + (self._unavailable_reason or "")
            )
        token = ciphertext.encode("utf-8")
        plaintext = self._fernet.decrypt(token)
        return plaintext.decode("utf-8")


def detect_credential(text: str) -> bool:
    """检测文本中是否包含凭证信息

    遍历所有凭证模式，匹配任一即返回 True。
    threshold 参数（预测用）：文本中凭"证字符占比"超过阈值也算。
    """
    for pattern in CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return True

    # 额外启发式：如果文本行中包含非常长的随机字符串
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        # 长度 > 40 且包含足够多的字母数字非空格字符
        if len(line) >= 40:
            alnum = sum(1 for c in line if c.isalnum() or c in "-_.")
            if alnum / max(len(line), 1) > 0.85:
                return True

    return False


# ── 密钥生成 ──────────────────────────────────────────

def generate_key() -> str:
    """生成新的 Fernet 密钥"""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("utf-8")


def save_key_to_file(key: str, path: str = KEY_FILE) -> str:
    """保存密钥到文件，返回文件路径"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(key + "\n")
    os.chmod(path, 0o600)  # 仅所有者可读写
    return path
