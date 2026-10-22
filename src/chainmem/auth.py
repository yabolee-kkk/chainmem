"""Per-agent authentication for the MCP HTTP transport."""

from __future__ import annotations

import hmac
from pathlib import Path


def load_agent_tokens(path: str) -> dict[str, str]:
    """Load a token-to-agent registry from a mode-600 file."""
    token_path = Path(path).expanduser()
    try:
        mode = token_path.stat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"agent tokens file not found: {token_path}") from exc
    if mode & 0o077:
        raise ValueError("agent tokens file must have mode 600 or stricter")

    registry: dict[str, str] = {}
    agents: set[str] = set()
    for line_number, raw_line in enumerate(
        token_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid agent token entry on line {line_number}")
        agent_id, token = line.split(":", 1)
        agent_id = agent_id.strip()
        token = token.strip()
        if not agent_id or not token:
            raise ValueError(f"invalid agent token entry on line {line_number}")
        if agent_id in agents:
            raise ValueError(f"duplicate agent: {agent_id}")
        if token in registry:
            raise ValueError(f"duplicate token on line {line_number}")
        agents.add(agent_id)
        registry[token] = agent_id
    if not registry:
        raise ValueError("agent tokens file contains no entries")
    return registry


def authenticate_token(registry: dict[str, str], token: str) -> str | None:
    """Return the agent mapped to token using a constant-time comparison."""
    if not token:
        return None
    for expected, agent_id in registry.items():
        if hmac.compare_digest(expected, token):
            return agent_id
    return None
