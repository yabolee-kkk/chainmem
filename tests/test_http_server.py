"""Integration coverage for the documented HTTP MCP endpoint."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _post_mcp(port: int, payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
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
        }
    finally:
        process.terminate()
        process.wait(timeout=10)
