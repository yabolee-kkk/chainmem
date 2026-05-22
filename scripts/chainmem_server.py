#!/usr/bin/env python3
"""ChainMem 持久化 MCP 服务 - 异步非阻塞版"""
import asyncio, json, os, sys, functools
sys.path.insert(0, os.path.expanduser("~/chainmem/src"))

from chainmem import ChainMemory

DB = os.path.expanduser("~/.chainmem/data.db")
SOCKET = "/tmp/chainmem.sock"

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

    server = await asyncio.start_unix_server(handle, path=SOCKET)
    os.chmod(SOCKET, 0o666)
    print(f"服务启动: {SOCKET}", file=sys.stderr)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
