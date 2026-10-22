"""Tests for per-agent MCP authentication."""

from __future__ import annotations

import os

import pytest

from chainmem.auth import authenticate_token, load_agent_tokens


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

    registry = load_agent_tokens(str(path))

    assert authenticate_token(registry, "token-one") == "hermes"
    assert authenticate_token(registry, "token-two") == "codex"
    assert authenticate_token(registry, "unknown") is None


def test_load_agent_tokens_rejects_duplicate_agents(tmp_path):
    path = tmp_path / "agents.tokens"
    path.write_text("hermes:token-one\nhermes:token-two\n")
    os.chmod(path, 0o600)

    with pytest.raises(ValueError, match="duplicate agent"):
        load_agent_tokens(str(path))
