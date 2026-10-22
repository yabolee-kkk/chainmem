"""Integration coverage for the documented HTTP MCP endpoint."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post_mcp(port: int, payload: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.loads(response.read())


def test_http_mcp_endpoint_lists_tools(tmp_path):
    port = _free_port()
    socket_path = str(tmp_path / "chainmem.sock")
    db_path = str(tmp_path / "data.db")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "chainmem.cli.app",
            "serve",
            "--socket",
            socket_path,
            "--http-port",
            str(port),
            "--db",
            db_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=2
                ) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.25)
        else:
            raise AssertionError("ChainMem HTTP server did not become healthy")

        result = _post_mcp(
            port,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            },
        )

        assert result["jsonrpc"] == "2.0"
        assert result["id"] == 1
        assert {
            tool["name"] for tool in result["result"]["tools"]
        } == {
            "chainmem_ingest",
            "chainmem_retrieve",
            "chainmem_stats",
            "work_memory_record",
            "work_memory_recall",
            "work_memory_timeline",
            "work_memory_get",
        }
    finally:
        process.terminate()
        process.wait(timeout=10)


def _wait_for_health(port: int, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("ChainMem server exited before becoming healthy")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=2
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise AssertionError("ChainMem HTTP server did not become healthy")


def test_http_work_memory_authentication_and_identity(tmp_path):
    port = _free_port()
    socket_path = str(tmp_path / "chainmem.sock")
    db_path = str(tmp_path / "data.db")
    tokens_path = tmp_path / "agents.tokens"
    tokens_path.write_text("hermes:hermes-token\ncodex:codex-token\n")
    os.chmod(tokens_path, 0o600)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "chainmem.cli.app",
            "serve",
            "--socket",
            socket_path,
            "--http-port",
            str(port),
            "--db",
            db_path,
            "--agent-tokens-file",
            str(tokens_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_for_health(port, process)
        tools_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        try:
            _post_mcp(port, tools_request)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("unauthenticated MCP request was accepted")

        occurred_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        record = _post_mcp(
            port,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "work_memory_record",
                    "arguments": {
                        "agent_id": "codex",
                        "task_summary": "完成 ChainMem 局域网记忆服务部署",
                        "project": "chainmem",
                        "status": "done",
                        "occurred_at": occurred_at,
                    },
                },
            },
            token="hermes-token",
        )
        assert "事件" in record["result"]["content"][0]["text"]

        recall = _post_mcp(
            port,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "work_memory_recall",
                    "arguments": {
                        "query": "局域网记忆服务",
                        "days": 7,
                    },
                },
            },
            token="codex-token",
        )
        text = recall["result"]["content"][0]["text"]
        assert "hermes" in text
        assert "完成 ChainMem 局域网记忆服务部署" in text
        assert "codex" not in text
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_non_loopback_server_requires_agent_tokens(tmp_path):
    port = _free_port()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "chainmem.cli.app",
            "serve",
            "--http-host",
            "0.0.0.0",
            "--http-port",
            str(port),
            "--socket",
            str(tmp_path / "chainmem.sock"),
            "--db",
            str(tmp_path / "data.db"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "agent-tokens-file" in (result.stdout + result.stderr)
