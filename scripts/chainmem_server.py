#!/usr/bin/env python3
"""ChainMem 持久化 MCP 服务 - HTTP + Unix Socket 双传输"""
import asyncio, json, os, sys, functools, io
sys.path.insert(0, os.path.expanduser("~/chainmem/src"))

from chainmem import ChainMemory

DB = os.path.expanduser("~/.chainmem/data.db")
SOCKET = "/tmp/chainmem.sock"
HTTP_PORT = 3115  # MCP HTTP 端口

# 持久化连接（模型在模块级只加载一次）
_cm = None


def get_cm():
    global _cm
    if _cm is None:
        _cm = ChainMemory(db_path=DB).open()
        # 优先从磁盘加载 FAISS 索引，失败才全量重建
        loaded = _cm.retriever.load_index()
        if loaded:
            print("FAISS 索引从磁盘加载完成", file=sys.stderr)
        else:
            print("磁盘无缓存索引，全量重建...", file=sys.stderr)
            _cm.retriever.rebuild_index()
    return _cm


async def handle(reader, writer):
    addr = writer.get_extra_info("peername")
    cm = get_cm()
    loop = asyncio.get_running_loop()
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
                resp = {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "tools": [
                            {"name": "chainmem_ingest",
                             "description": "结链：将文本存储为链式记忆",
                             "inputSchema": {"type": "object",
                                             "properties": {
                                                 "text": {"type": "string"},
                                                 "source": {"type": "string"},
                                                 "tags": {"type": "string"}},
                                             "required": ["text"]}},
                            {"name": "chainmem_retrieve",
                             "description": "追溯：输入查询，还原完整记忆链",
                             "inputSchema": {"type": "object",
                                             "properties": {
                                                 "query": {"type": "string"},
                                                 "tags": {"type": "string"}},
                                             "required": ["query"]}},
                            {"name": "chainmem_stats",
                             "description": "查看记忆统计",
                             "inputSchema": {"type": "object", "properties": {}}},
                        ]
                    }
                }
                writer.write((json.dumps(resp) + "\n").encode())

            elif method == "tools/call":
                tool = req.get("params", {}).get("name")
                args = req.get("params", {}).get("arguments", {})

                if tool == "chainmem_stats":
                    s = cm.stats()
                    resp = {"jsonrpc": "2.0", "id": rid,
                            "result": {"content": [{"type": "text",
                                                     "text": f"链总数: {s['chains']}\n节点总数: {s['nodes']}\n数据库: {s['db_path']}"}]}}
                    writer.write((json.dumps(resp) + "\n").encode())

                elif tool == "chainmem_retrieve":
                    # 检索很快（~22ms），直接在 async 协程中执行
                    query = args.get("query", "")
                    tags_str = args.get("tags", "")
                    tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
                    results = cm.retrieve(query, tags=tag_list or None)
                    text = "".join(results) if results else "未找到匹配的记忆"
                    resp = {"jsonrpc": "2.0", "id": rid,
                            "result": {"content": [{"type": "text", "text": text}]}}
                    writer.write((json.dumps(resp) + "\n").encode())

                elif tool == "chainmem_ingest":
                    # ★ 在线程池中执行 ingest（CPU 密集：切块 + 编码），不阻塞服务器
                    text = args.get("text", "")
                    source = args.get("source", "")
                    tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]

                    def _do_ingest():
                        chain = cm.ingest(text, source=source, tags=tags)
                        # 增量添加到 FAISS 索引（无需全量重建）
                        nodes = chain.nodes
                        if nodes:
                            embeddings = np.array([n.embedding for n in nodes])
                            cm.retriever.add_nodes(
                                embeddings=embeddings,
                                node_ids=[n.id for n in nodes],
                                texts=[n.text for n in nodes],
                                chain_ids=[n.chain_id for n in nodes],
                                next_ids=[n.next_id for n in nodes],
                                seqs=[n.seq for n in nodes],
                                prev_ids=[n.prev_id for n in nodes],
                            )
                        return chain

                    import numpy as np
                    chain = await loop.run_in_executor(None, _do_ingest)
                    resp = {"jsonrpc": "2.0", "id": rid,
                            "result": {"content": [{"type": "text",
                                                     "text": f"结链成功：{chain.node_count} 个节点，前缀「{chain.anchor_prefix}」"}]}}
                    writer.write((json.dumps(resp) + "\n").encode())

                else:
                    resp = {"jsonrpc": "2.0", "id": rid,
                            "error": {"code": -32601, "message": f"未知工具: {tool}"}}
                    writer.write((json.dumps(resp) + "\n").encode())

            elif method == "initialize":
                resp = {"jsonrpc": "2.0", "id": rid,
                        "result": {"protocolVersion": "2025-11-25",
                                   "capabilities": {"tools": {}},
                                   "serverInfo": {"name": "chainmem", "version": "0.1.0"}}}
                writer.write((json.dumps(resp) + "\n").encode())

            elif method == "notifications/initialized":
                pass

            else:
                resp = {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": f"未知方法: {method}"}}
                writer.write((json.dumps(resp) + "\n").encode())

            await writer.drain()
    except asyncio.TimeoutError:
        pass
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        print(f"处理错误: {e}", file=sys.stderr)
    finally:
        try:
            writer.close()
        except:
            pass


async def main():
    if os.path.exists(SOCKET):
        os.unlink(SOCKET)
    # 预加载（在 main() 同步阶段完成，不阻塞 accept）
    print("正在加载嵌入模型...", file=sys.stderr)
    cm = get_cm()
    print(f"模型就绪！{cm.stats()['nodes']} 个节点", file=sys.stderr)

    # 1) Unix Socket 传输
    server = await asyncio.start_unix_server(handle, path=SOCKET)
    os.chmod(SOCKET, 0o666)
    print(f"Unix Socket 服务启动: {SOCKET}", file=sys.stderr)

    # 2) HTTP 传输（MCP Streamable HTTP）
    http_server = await asyncio.start_server(handle_http, "127.0.0.1", HTTP_PORT)
    print(f"HTTP 服务启动: http://127.0.0.1:{HTTP_PORT}/mcp", file=sys.stderr)

    async with server:
        async with http_server:
            await asyncio.gather(
                server.serve_forever(),
                http_server.serve_forever(),
            )


async def handle_http(reader, writer):
    """MCP HTTP 传输处理器"""
    try:
        data = await asyncio.wait_for(reader.read(65536), timeout=30)
        if not data:
            writer.close()
            return

        request_text = data.decode("utf-8", errors="replace")
        # 解析 HTTP 请求
        lines = request_text.split("\r\n")
        if not lines:
            writer.close()
            return

        first_line = lines[0]
        method, path, _ = first_line.split(" ", 2)

        # 找空行分隔头部和 body
        blank_idx = request_text.find("\r\n\r\n")
        body = ""
        if blank_idx != -1:
            body = request_text[blank_idx + 4:]

        if method == "GET" and path == "/health":
            cm = get_cm()
            s = cm.stats()
            resp_body = json.dumps({
                "status": "healthy",
                "chains": s["chains"],
                "nodes": s["nodes"],
                "db_path": s["db_path"],
            })
            http_response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(resp_body.encode())}\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "\r\n"
                f"{resp_body}"
            )
            writer.write(http_response.encode())
            await writer.drain()

        elif method in ("POST",) and path in ("/mcp", "/"):
            req = json.loads(body)
            rid = req.get("id")
            mcp_method = req.get("method")

            cm = get_cm()
            resp = process_mcp_request(cm, rid, mcp_method, req)

            resp_body = json.dumps(resp)
            http_response = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(resp_body.encode())}\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "\r\n"
                f"{resp_body}"
            )
            writer.write(http_response.encode())
            await writer.drain()

        elif method == "OPTIONS":
            http_response = (
                "HTTP/1.1 204 No Content\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Access-Control-Allow-Methods: POST, GET, OPTIONS\r\n"
                "Access-Control-Allow-Headers: Content-Type\r\n"
                "\r\n"
            )
            writer.write(http_response.encode())
            await writer.drain()

        else:
            path_clean = path.split('?')[0]
            cm = get_cm()

            # GET / — serve dashboard
            if method == "GET" and path_clean == "/":
                html_path = os.path.expanduser("~/chainmem/dashboard.html")
                try:
                    with open(html_path, "r", encoding="utf-8") as f:
                        html = f.read()
                    resp_body = html.encode("utf-8")
                    http_response = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        f"Content-Length: {len(resp_body)}\r\n"
                        "Access-Control-Allow-Origin: *\r\n"
                        "\r\n"
                    )
                    writer.write(http_response.encode())
                    writer.write(resp_body)
                except FileNotFoundError:
                    http_response = "HTTP/1.1 404 Not Found\r\n\r\nDashboard file not found"
                    writer.write(http_response.encode())

            # GET /api/chains — list all chains
            elif method == "GET" and path_clean == "/api/chains":
                import sqlite3
                db_path = cm.db_path if hasattr(cm, 'db_path') else os.path.expanduser("~/.chainmem/data.db")
                chains = []
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute("""
                        SELECT id, anchor_prefix, node_count, tags, created_at
                        FROM chains
                        ORDER BY created_at DESC
                        LIMIT 200
                    """)
                    for row in cur.fetchall():
                        ch = dict(row)
                        # Get preview (first node text)
                        preview = ""
                        try:
                            node = conn.execute(
                                "SELECT text FROM nodes WHERE chain_id=? ORDER BY seq LIMIT 1",
                                (ch["id"],)
                            ).fetchone()
                            if node:
                                preview = node["text"][:200]
                        except Exception:
                            pass
                        ch["preview"] = preview
                        chains.append(ch)
                    conn.close()
                except Exception as e:
                    resp_body = json.dumps({"error": str(e)})
                    http_response = (
                        "HTTP/1.1 500 Internal Server Error\r\n"
                        "Content-Type: application/json\r\n"
                        f"Content-Length: {len(resp_body.encode())}\r\n"
                        "Access-Control-Allow-Origin: *\r\n"
                        "\r\n" + resp_body
                    )
                    writer.write(http_response.encode())
                    await writer.drain()
                    return

                resp_body = json.dumps({"chains": chains}, ensure_ascii=False)
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(resp_body.encode())}\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "\r\n"
                    f"{resp_body}"
                )
                writer.write(http_response.encode())

            # GET /api/chain/{id} — get full chain
            elif method == "GET" and path_clean.startswith("/api/chain/"):
                chain_id = path_clean[len("/api/chain/"):]
                import sqlite3
                db_path = cm.db_path if hasattr(cm, 'db_path') else os.path.expanduser("~/.chainmem/data.db")
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    ch_row = conn.execute(
                        "SELECT * FROM chains WHERE id=?", (chain_id,)
                    ).fetchone()
                    if not ch_row:
                        resp_body = json.dumps({"error": "Chain not found"})
                        http_response = "HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\n"
                        http_response += f"Content-Length: {len(resp_body.encode())}\r\n"
                        http_response += "Access-Control-Allow-Origin: *\r\n\r\n" + resp_body
                        writer.write(http_response.encode())
                        await writer.drain()
                        return
                    chain = dict(ch_row)
                    # Get all nodes in order
                    nodes = conn.execute(
                        "SELECT seq, text FROM nodes WHERE chain_id=? ORDER BY seq",
                        (chain_id,)
                    ).fetchall()
                    chain["nodes"] = [dict(n) for n in nodes]
                    chain["content"] = "\n".join(n["text"] for n in nodes)
                    conn.close()
                except Exception as e:
                    resp_body = json.dumps({"error": str(e)})
                    http_response = (
                        "HTTP/1.1 500 Internal Server Error\r\n"
                        "Content-Type: application/json\r\n"
                        f"Content-Length: {len(resp_body.encode())}\r\n"
                        "Access-Control-Allow-Origin: *\r\n"
                        "\r\n" + resp_body
                    )
                    writer.write(http_response.encode())
                    await writer.drain()
                    return

                resp_body = json.dumps(chain, ensure_ascii=False)
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(resp_body.encode())}\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "\r\n"
                    f"{resp_body}"
                )
                writer.write(http_response.encode())

            # POST /api/search — search memories
            elif method == "POST" and path_clean == "/api/search":
                try:
                    req_body = json.loads(body)
                    query = req_body.get("query", "")
                    tags_str = req_body.get("tags", "")
                    tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
                    # Use MCP retrieve for semantic search
                    import numpy as np
                    results = cm.retrieve(query, tags=tag_list or None)
                    if results:
                        text = "".join(results)
                        # Get chain info
                        import sqlite3
                        db_path = cm.db_path if hasattr(cm, 'db_path') else os.path.expanduser("~/.chainmem/data.db")
                        conn = sqlite3.connect(db_path)
                        conn.row_factory = sqlite3.Row
                        chain_rows = conn.execute(
                            "SELECT id, anchor_prefix, tags, node_count FROM chains ORDER BY created_at DESC LIMIT 20"
                        ).fetchall()
                        conn.close()
                        result_items = []
                        for cr in chain_rows:
                            cd = dict(cr)
                            # Get first 200 chars of content for preview
                            result_items.append({
                                "chain_id": cd["id"],
                                "anchor_prefix": cd["anchor_prefix"],
                                "tags": cd["tags"],
                                "node_count": cd["node_count"],
                                "content": text[:500],
                                "score": 1.0,
                            })
                        resp_body = json.dumps({"results": result_items}, ensure_ascii=False)
                    else:
                        resp_body = json.dumps({"results": []})
                except Exception as e:
                    resp_body = json.dumps({"error": str(e)})
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(resp_body.encode())}\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "\r\n"
                    f"{resp_body}"
                )
                writer.write(http_response.encode())

            # POST /api/ingest — store a new memory
            elif method == "POST" and path_clean == "/api/ingest":
                try:
                    req_body = json.loads(body)
                    text = req_body.get("text", "")
                    source = req_body.get("source", "dashboard")
                    tags_str = req_body.get("tags", "hermes")
                    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
                    if not text:
                        resp_body = json.dumps({"error": "text is required"})
                    else:
                        loop = asyncio.get_running_loop()
                        chain = await loop.run_in_executor(
                            None,
                            lambda: _do_dashboard_ingest(cm, text, source, tags)
                        )
                        resp_body = json.dumps({
                            "chain_id": chain.id,
                            "node_count": chain.node_count,
                            "anchor_prefix": chain.anchor_prefix,
                        }, ensure_ascii=False)
                except Exception as e:
                    resp_body = json.dumps({"error": str(e)})
                http_response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(resp_body.encode())}\r\n"
                    "Access-Control-Allow-Origin: *\r\n"
                    "\r\n"
                    f"{resp_body}"
                )
                writer.write(http_response.encode())

            else:
                http_response = "HTTP/1.1 404 Not Found\r\n\r\nNot Found"
                writer.write(http_response.encode())

            await writer.drain()

    except asyncio.TimeoutError:
        pass
    except Exception as e:
        print(f"HTTP 处理错误: {e}", file=sys.stderr)
    finally:
        try:
            writer.close()
        except:
            pass


def process_mcp_request(cm, rid, mcp_method, req):
    """处理 MCP JSON-RPC 请求，返回响应字典"""
    if mcp_method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": rid,
            "result": {
                "tools": [
                    {"name": "chainmem_ingest",
                     "description": "结链：将文本存储为链式记忆",
                     "inputSchema": {"type": "object",
                                     "properties": {
                                         "text": {"type": "string"},
                                         "source": {"type": "string"},
                                         "tags": {"type": "string"}},
                                     "required": ["text"]}},
                    {"name": "chainmem_retrieve",
                     "description": "追溯：输入查询，还原完整记忆链",
                     "inputSchema": {"type": "object",
                                     "properties": {
                                         "query": {"type": "string"},
                                         "tags": {"type": "string"}},
                                     "required": ["query"]}},
                    {"name": "chainmem_stats",
                     "description": "查看记忆统计",
                     "inputSchema": {"type": "object", "properties": {}}},
                ]
            }
        }

    elif mcp_method == "tools/call":
        tool = req.get("params", {}).get("name")
        args = req.get("params", {}).get("arguments", {})

        if tool == "chainmem_stats":
            s = cm.stats()
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                             "text": f"链总数: {s['chains']}\n节点总数: {s['nodes']}\n数据库: {s['db_path']}"}]}}

        elif tool == "chainmem_retrieve":
            query = args.get("query", "")
            tags_str = args.get("tags", "")
            tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            results = cm.retrieve(query, tags=tag_list or None)
            text = "".join(results) if results else "未找到匹配的记忆"
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}]}}

        elif tool == "chainmem_ingest":
            import numpy as np
            text = args.get("text", "")
            source = args.get("source", "")
            tags = [t.strip() for t in args.get("tags", "").split(",") if t.strip()]
            chain = cm.ingest(text, source=source, tags=tags)
            nodes = chain.nodes
            if nodes:
                embeddings = np.array([n.embedding for n in nodes])
                cm.retriever.add_nodes(
                    embeddings=embeddings,
                    node_ids=[n.id for n in nodes],
                    texts=[n.text for n in nodes],
                    chain_ids=[n.chain_id for n in nodes],
                    next_ids=[n.next_id for n in nodes],
                    seqs=[n.seq for n in nodes],
                    prev_ids=[n.prev_id for n in nodes],
                )
            return {"jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text",
                                             "text": f"结链成功：{chain.node_count} 个节点，前缀「{chain.anchor_prefix}」"}]}}

        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {tool}"}}

    elif mcp_method == "initialize":
        return {"jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": "2025-11-25",
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "chainmem", "version": "0.1.0"}}}

    elif mcp_method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"未知方法: {mcp_method}"}}


def _do_dashboard_ingest(cm, text, source, tags):
    """ingest helper for dashboard API (runs in thread pool)."""
    import numpy as np
    chain = cm.ingest(text, source=source, tags=tags)
    nodes = chain.nodes
    if nodes:
        embeddings = np.array([n.embedding for n in nodes])
        cm.retriever.add_nodes(
            embeddings=embeddings,
            node_ids=[n.id for n in nodes],
            texts=[n.text for n in nodes],
            chain_ids=[n.chain_id for n in nodes],
            next_ids=[n.next_id for n in nodes],
            seqs=[n.seq for n in nodes],
            prev_ids=[n.prev_id for n in nodes],
        )
    return chain


if __name__ == "__main__":
    asyncio.run(main())
