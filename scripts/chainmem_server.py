#!/usr/bin/env python3
"""ChainMem 持久化 MCP 服务 - HTTP + Unix Socket 双传输"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.expanduser("~/chainmem/src"))

from chainmem import ChainMemory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from consolidation import ConsolidationScheduler, run_consolidation

DB = os.path.expanduser("~/.chainmem/data.db")
SOCKET = "/tmp/chainmem.sock"
HTTP_PORT = 3115

_cm = None
_scheduler = None


# =========================================================================
# 响应辅助函数 — 消除重复的 HTTP/MCP 响应模板
# =========================================================================

def _http_ok(writer, body: bytes, content_type: str = "application/json"):
    """发送 200 HTTP 响应 + CORS 头。body 为已编码的 bytes。"""
    resp = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n"
    ).encode() + body
    writer.write(resp)

def _http_json(writer, data):
    """便捷：发送 200 + JSON。data 是 Python 对象。"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    _http_ok(writer, body)

def _http_err(writer, msg: str, status: int = 500):
    """发送错误 HTTP 响应。"""
    body = json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8")
    resp = (
        f"HTTP/1.1 {status} {'Error' if status != 404 else 'Not Found'}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "\r\n"
    ).encode() + body
    writer.write(resp)

def _http_html(writer, html: bytes):
    """发送 200 + HTML。"""
    _http_ok(writer, html, content_type="text/html; charset=utf-8")

async def _http_drain(writer):
    """发送缓冲区并等待写入完成。"""
    await writer.drain()


# --- MCP (Unix Socket) 响应辅助 ---

def _mcp_result(rid, data: dict) -> str:
    """构建 MCP 成功响应。"""
    return json.dumps({"jsonrpc": "2.0", "id": rid, "result": data}) + "\n"

def _mcp_error(rid, msg: str, code: int = -32601) -> str:
    """构建 MCP 错误响应。"""
    return json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}) + "\n"

def _mcp_tcontent(text: str) -> dict:
    """构建 MCP content 数组（单个 text 元素）。"""
    return {"content": [{"type": "text", "text": text}]}

def _mcp_write(writer, data: str):
    """向 Unix Socket 写入一行。"""
    writer.write(data.encode())


# =========================================================================
# 工具函数 — MCP tool handlers
# =========================================================================

def _tools_list() -> dict:
    """tools/list — 返回可用工具列表。"""
    return {
        "tools": [
            {"name": "chainmem_ingest", "description": "结链：将文本存储为链式记忆",
             "inputSchema": {"type": "object", "properties": {
                 "text": {"type": "string"}, "source": {"type": "string"},
                 "tags": {"type": "string"}}, "required": ["text"]}},
            {"name": "chainmem_retrieve", "description": "追溯：输入查询，还原完整记忆链",
             "inputSchema": {"type": "object", "properties": {
                 "query": {"type": "string"}, "tags": {"type": "string"}},
             "required": ["query"]}},
            {"name": "chainmem_stats", "description": "查看记忆统计",
             "inputSchema": {"type": "object", "properties": {}}},
        ]
    }

def _tool_stats(cm) -> dict:
    """chainmem_stats — 记忆统计。"""
    s = cm.stats()
    return _mcp_tcontent(f"链总数: {s['chains']}\n节点总数: {s['nodes']}\n数据库: {s['db_path']}")

def _tool_retrieve(cm, args: dict) -> dict:
    """chainmem_retrieve — 语义搜索。"""
    query = args.get("query", "")
    tags_str = args.get("tags", "")
    tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
    results = cm.retrieve(query, tags=tag_list or None)
    text = "".join(results) if results else "未找到匹配的记忆"
    return _mcp_tcontent(text)

def _tool_ingest(cm, args: dict) -> dict:
    """chainmem_ingest — 存储新记忆。"""
    import numpy as np
    text = args.get("text", "")
    source = args.get("source", "")
    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]
    chain = cm.ingest(text, source=source, tags=tags)
    nodes = chain.nodes
    if nodes:
        embeddings = np.array([n.embedding for n in nodes])
        cm.retriever.add_nodes(
            embeddings=embeddings, node_ids=[n.id for n in nodes],
            texts=[n.text for n in nodes], chain_ids=[n.chain_id for n in nodes],
            next_ids=[n.next_id for n in nodes], seqs=[n.seq for n in nodes],
            prev_ids=[n.prev_id for n in nodes],
        )
    return _mcp_tcontent(f"结链成功：{chain.node_count} 个节点，前缀「{chain.anchor_prefix}」")


# =========================================================================
# 数据库查询辅助
# =========================================================================

def _chain_db_path(cm) -> str:
    """获取 ChainMem 数据库路径。"""
    return cm.db_path if hasattr(cm, 'db_path') else os.path.expanduser("~/.chainmem/data.db")

def _db_get_chains(db_path: str, limit: int = 200, cursor: int = 0, insight: bool = None):
    """查询链列表，每链附带预览。返回 list[dict]。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conditions = ["c.rowid > ?"]
    params = [cursor]
    if insight is not None:
        if insight:
            conditions.append("(c.tags LIKE '%_consolidation%')")
        else:
            conditions.append("(c.tags IS NULL OR c.tags NOT LIKE '%_consolidation%')")

    rows = conn.execute(f"""
        SELECT id, anchor_prefix, node_count, tags, created_at
        FROM chains c
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC LIMIT ?
    """, (*params, limit)).fetchall()

    chains = []
    for r in rows:
        ch = dict(r)
        node = conn.execute(
            "SELECT text FROM nodes WHERE chain_id=? ORDER BY seq LIMIT 1",
            (ch["id"],)
        ).fetchone()
        ch["preview"] = (node["text"][:200] if node else "")
        chains.append(ch)
    conn.close()
    return chains

def _db_get_chain(db_path: str, chain_id: str) -> dict:
    """查询单条链（含所有节点）。返回 dict 或 None。"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM chains WHERE id=?", (chain_id,)).fetchone()
    if not row:
        conn.close()
        return None
    chain = dict(row)
    nodes = conn.execute(
        "SELECT seq, text FROM nodes WHERE chain_id=? ORDER BY seq", (chain_id,)
    ).fetchall()
    chain["nodes"] = [dict(n) for n in nodes]
    chain["content"] = "\n".join(n["text"] for n in nodes)
    conn.close()
    return chain


# =========================================================================
# Unix Socket MCP 处理器
# =========================================================================

async def handle(reader, writer):
    cm = get_cm()
    try:
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line:
                break
            text = line.strip().decode()
            if not text:
                continue
            req = json.loads(text)
            rid = req.get("id")
            method = req.get("method")

            if method == "tools/list":
                _mcp_write(writer, _mcp_result(rid, _tools_list()))

            elif method == "tools/call":
                tool = req.get("params", {}).get("name")
                args = req.get("params", {}).get("arguments", {})
                if tool == "chainmem_stats":
                    _mcp_write(writer, _mcp_result(rid, _tool_stats(cm)))
                elif tool == "chainmem_retrieve":
                    _mcp_write(writer, _mcp_result(rid, _tool_retrieve(cm, args)))
                elif tool == "chainmem_ingest":
                    _mcp_write(writer, _mcp_result(rid, _tool_ingest(cm, args)))
                else:
                    _mcp_write(writer, _mcp_error(rid, f"未知工具: {tool}"))

            elif method == "initialize":
                _mcp_write(writer, _mcp_result(rid, {
                    "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "chainmem", "version": "0.1.0"}}))

            elif method == "notifications/initialized":
                pass

            else:
                _mcp_write(writer, _mcp_error(rid, f"未知方法: {method}"))

            await writer.drain()
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        print(f"处理错误: {e}", file=sys.stderr)
    finally:
        try:
            writer.close()
        except:
            pass


# =========================================================================
# HTTP 处理器
# =========================================================================

async def handle_http(reader, writer):
    global _scheduler
    try:
        data = await asyncio.wait_for(reader.read(65536), timeout=30)
        if not data:
            writer.close()
            return

        request_text = data.decode("utf-8", errors="replace")
        lines = request_text.split("\r\n")
        if not lines:
            writer.close()
            return

        first_line = lines[0]
        method, path, _ = first_line.split(" ", 2)
        blank_idx = request_text.find("\r\n\r\n")
        body = request_text[blank_idx + 4:] if blank_idx != -1 else ""

        path_clean = path.split('?')[0]
        cm = get_cm()

        # ── GET /health ──────────────────────────────────────────────
        if method == "GET" and path == "/health":
            s = cm.stats()
            _http_json(writer, {"status": "healthy", "chains": s["chains"],
                                "nodes": s["nodes"], "db_path": s["db_path"]})

        # ── POST /mcp ────────────────────────────────────────────────
        elif method in ("POST",) and path in ("/mcp", "/"):
            req = json.loads(body)
            rid, mcp_method = req.get("id"), req.get("method")
            _http_json(writer, process_mcp_request(cm, rid, mcp_method, req))

        # ── OPTIONS (CORS) ───────────────────────────────────────────
        elif method == "OPTIONS":
            resp = "HTTP/1.1 204 No Content\r\n" \
                   "Access-Control-Allow-Origin: *\r\n" \
                   "Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n" \
                   "Access-Control-Allow-Headers: Content-Type\r\n\r\n"
            writer.write(resp.encode())

        # ── GET / — HTML dashboard ───────────────────────────────────
        elif method == "GET" and path_clean == "/":
            html_path = os.path.expanduser("~/chainmem/dashboard.html")
            try:
                with open(html_path, "rb") as f:
                    _http_html(writer, f.read())
            except FileNotFoundError:
                _http_err(writer, "Dashboard file not found", 404)

        # ── GET /api/chains ──────────────────────────────────────────
        elif method == "GET" and path_clean == "/api/chains":
            chains = _db_get_chains(_chain_db_path(cm))
            _http_json(writer, {"chains": chains})

        # ── GET /api/chain/{id} ──────────────────────────────────────
        elif method == "GET" and path_clean.startswith("/api/chain/"):
            chain_id = path_clean[len("/api/chain/"):]
            chain = _db_get_chain(_chain_db_path(cm), chain_id)
            if chain is None:
                _http_err(writer, "Chain not found", 404)
            else:
                _http_json(writer, chain)

        # ── POST /api/search ─────────────────────────────────────────
        elif method == "POST" and path_clean == "/api/search":
            try:
                req_body = json.loads(body)
                query = req_body.get("query", "")
                tags_str = req_body.get("tags", "")
                tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
                results = cm.retrieve(query, tags=tag_list or None)
                if results:
                    chains = _db_get_chains(_chain_db_path(cm), limit=20)
                    result_items = []
                    for c in chains:
                        result_items.append({
                            "chain_id": c["id"], "anchor_prefix": c["anchor_prefix"],
                            "tags": c["tags"], "node_count": c["node_count"],
                            "content": "".join(results)[:500], "score": 1.0,
                        })
                    _http_json(writer, {"results": result_items})
                else:
                    _http_json(writer, {"results": []})
            except Exception as e:
                _http_json(writer, {"error": str(e)})

        # ── POST /api/ingest ─────────────────────────────────────────
        elif method == "POST" and path_clean == "/api/ingest":
            try:
                req_body = json.loads(body)
                text = req_body.get("text", "")
                source = req_body.get("source", "dashboard")
                tags_str = req_body.get("tags", "hermes")
                tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                if not text:
                    _http_json(writer, {"error": "text is required"})
                else:
                    loop = asyncio.get_running_loop()
                    chain = await loop.run_in_executor(
                        None, lambda: _do_dashboard_ingest(cm, text, source, tags)
                    )
                    _http_json(writer, {
                        "chain_id": chain.id, "node_count": chain.node_count,
                        "anchor_prefix": chain.anchor_prefix,
                    })
            except Exception as e:
                _http_json(writer, {"error": str(e)})

        # ── POST /api/consolidate ────────────────────────────────────
        elif method == "POST" and path_clean == "/api/consolidate":
            if _scheduler:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, _scheduler.trigger_now)
            else:
                llm_config = {
                    "url": os.environ.get("CHAINMEM_LLM_URL",
                        "https://api.deepseek.com/v1/chat/completions"),
                    "api_key": os.environ.get("CHAINMEM_LLM_KEY",
                        os.environ.get("DEEPSEEK_API_KEY", "")),
                    "model": os.environ.get("CHAINMEM_LLM_MODEL", "deepseek-chat"),
                }
                result = run_consolidation(cm, DB, llm_config)
            _http_json(writer, result)

        # ── GET /api/consolidation/status ────────────────────────────
        elif method == "GET" and path_clean == "/api/consolidation/status":
            if _scheduler:
                _http_json(writer, _scheduler.get_status())
            else:
                from consolidation import _load_state
                st = _load_state()
                st["status"] = "no_scheduler"
                _http_json(writer, st)

        # ── 404 ──────────────────────────────────────────────────────
        else:
            _http_err(writer, "Not Found", 404)

        await _http_drain(writer)

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        print(f"HTTP 处理错误: {e}", file=sys.stderr)
    finally:
        try:
            writer.close()
        except:
            pass


# =========================================================================
# MCP JSON-RPC 处理器（给 HTTP POST /mcp 用）
# =========================================================================

def process_mcp_request(cm, rid, mcp_method, req):
    if mcp_method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": _tools_list()}

    elif mcp_method == "tools/call":
        tool = req.get("params", {}).get("name")
        args = req.get("params", {}).get("arguments", {})
        if tool == "chainmem_stats":
            return {"jsonrpc": "2.0", "id": rid, "result": _tool_stats(cm)}
        elif tool == "chainmem_retrieve":
            return {"jsonrpc": "2.0", "id": rid, "result": _tool_retrieve(cm, args)}
        elif tool == "chainmem_ingest":
            return {"jsonrpc": "2.0", "id": rid, "result": _tool_ingest(cm, args)}
        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {tool}"}}

    elif mcp_method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
            "serverInfo": {"name": "chainmem", "version": "0.1.0"}}}

    elif mcp_method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"未知方法: {mcp_method}"}}


# =========================================================================
# 应用启动
# =========================================================================

async def main():
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)

    print("正在加载嵌入模型...", file=sys.stderr)
    cm = get_cm()
    print(f"模型就绪！{cm.stats()['nodes']} 个节点", file=sys.stderr)

    server = await asyncio.start_unix_server(handle, path=SOCKET)
    os.chmod(SOCKET, 0o666)
    print(f"Unix Socket 服务启动: {SOCKET}", file=sys.stderr)

    http_server = await asyncio.start_server(handle_http, "127.0.0.1", HTTP_PORT)
    print(f"HTTP 服务启动: http://127.0.0.1:{HTTP_PORT}/mcp", file=sys.stderr)

    global _scheduler
    _scheduler = ConsolidationScheduler(get_cm, DB)
    _scheduler.start()
    print("Consolidation 调度器已启动", file=sys.stderr)

    async with server:
        async with http_server:
            await asyncio.gather(
                server.serve_forever(),
                http_server.serve_forever(),
            )


def get_cm():
    global _cm
    if _cm is None:
        _cm = ChainMemory(db_path=DB).open()
        loaded = _cm.retriever.load_index()
        if loaded:
            print("FAISS 索引从磁盘加载完成", file=sys.stderr)
        else:
            print("磁盘无缓存索引，全量重建...", file=sys.stderr)
            _cm.retriever.rebuild_index()
    return _cm


def _do_dashboard_ingest(cm, text, source, tags):
    """ingest helper for dashboard API (runs in thread pool)."""
    import numpy as np
    chain = cm.ingest(text, source=source, tags=tags)
    nodes = chain.nodes
    if nodes:
        embeddings = np.array([n.embedding for n in nodes])
        cm.retriever.add_nodes(
            embeddings=embeddings, node_ids=[n.id for n in nodes],
            texts=[n.text for n in nodes], chain_ids=[n.chain_id for n in nodes],
            next_ids=[n.next_id for n in nodes], seqs=[n.seq for n in nodes],
            prev_ids=[n.prev_id for n in nodes],
        )
    return chain


if __name__ == "__main__":
    asyncio.run(main())
