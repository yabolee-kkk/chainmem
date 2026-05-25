"""pytest 配置"""

import pytest


def pytest_configure(config):
    """添加自定义标记"""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )
