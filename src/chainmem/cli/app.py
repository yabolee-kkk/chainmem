"""ChainMem CLI"""

import json
import os
import sys
import traceback
import asyncio
from pathlib import Path
from typing import Any

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from chainmem import ChainMemory

app = typer.Typer(help="ChainMem — 链式 + 向量混合记忆系统")
console = Console()
DEFAULT_DB = "~/.chainmem/data.db"


def _get_cm(db: str | None = None) -> ChainMemory:
    cm = ChainMemory(db_path=db or DEFAULT_DB)
    return cm.open()


@app.command()
def ingest(
    text: str = typer.Argument(..., help="要结链的文本"),
    source: str = typer.Option("", "--source", "-s", help="来源会话"),
    tags: str = typer.Option("", "--tags", "-t", help="标签（逗号分隔）"),
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """结链：文本 → 切块 → 嵌入 → 存储"""
    cm = _get_cm(db)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    chain = cm.ingest(text, source=source, tags=tag_list)

    panel = Panel(
        f"[bold green]✓ 结链成功[/bold green]\n\n"
        f"链 ID:    {chain.id}\n"
        f"节点数:   {chain.node_count}\n"
        f"前缀锚点: [bold]{chain.anchor_prefix}[/bold]\n"
        f"来源:     {source or '(未指定)'}\n\n"
        f"[dim]完整文本:[/dim]\n{chain.full_text()}",
        title="ChainMem Ingest",
    )
    rprint(panel)
    cm.close()


@app.command()
def retrieve(
    query: str = typer.Argument(..., help="查询文本（前缀或关键词）"),
    max_steps: int = typer.Option(100, "--max-steps", "-m", help="最大遍历步数"),
    tags: str = typer.Option("", "--tags", "-t", help="标签过滤（逗号分隔，OR 逻辑）"),
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """追溯：查询 → 最近邻 → 指针遍历 → 文本复原（支持标签过滤）"""
    cm = _get_cm(db)
    cm.retriever.rebuild_index()

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    results = cm.retrieve(query, max_steps=max_steps, tags=tag_list or None)
    cm.close()

    if not results:
        print("⚠ 未找到匹配的记忆")
        return

    print()
    for i, text in enumerate(results):
        marker = "🟢" if i == 0 else ("🔴" if i == len(results) - 1 else "🔵")
        print(f"  {marker} {text}")

    print()
    print("─" * 50)
    print("完整记忆重现：")
    print("".join(results))
    print("─" * 50)


@app.command()
def stats(
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """查看记忆统计"""
    cm = _get_cm(db)
    s = cm.stats()
    chains = cm.store.get_all_chains()
    cm.close()

    table = Table(title="ChainMem 统计")
    table.add_column("指标", style="cyan")
    table.add_column("值", style="green")
    table.add_row("数据库", s["db_path"])
    table.add_row("链总数", str(s["chains"]))
    table.add_row("节点总数", str(s["nodes"]))
    console.print(table)

    if chains:
        console.print("\n[bold]已存储的链:[/bold]")
        for c in chains:
            tag_str = ""
            tags_raw = c.get("tags", [])
            if isinstance(tags_raw, str):
                tags_raw = json.loads(tags_raw)
            if tags_raw:
                tag_str = f"  [cyan]{' '.join('#' + t for t in tags_raw)}[/cyan]"
            rprint(f"  [dim]{c['id'][:8]}...[/dim] 前缀=[bold]{c['anchor_prefix']}[/bold]  "
                   f"节点={c['node_count']}  强度={c['strength']:.1f}{tag_str}  "
                   f"[dim]{c['created_at']}[/dim]")


@app.command()
def demo():
    """运行快速演示"""
    import tempfile
    db = tempfile.mktemp(suffix=".db")

    cm = ChainMemory(db_path=db).open()

    texts = [
        "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条，这样只要想起开头几个字就能顺着把后面的内容推导出来。",
        "关于股决项目，我觉得应该先做好最薄弱的一环，然后让朋友内测、反馈、再扩，从不用登录墙开始。",
        "用户对医疗养老行业和全栈项目有广泛兴趣，但当前最关注的是股决A股投资APP项目。",
    ]

    for i, t in enumerate(texts):
        chain = cm.ingest(t, source=f"demo_session_{i}", tags=["demo"])
        rprint(f"[dim]✓ 已结链:[/dim] [bold]{chain.anchor_prefix}[/bold]... ({chain.node_count} 节点)")

    cm.retriever.rebuild_index()

    queries = [
        "其实我的想法",
        "关于股决",
    ]

    for q in queries:
        console.print(f"\n[bold]🔍 查询:[/bold] \"{q}\"")
        results = cm.retrieve(q)
        if results:
            for i, t in enumerate(results):
                marker = "🟢" if i == 0 else ("🔴" if i == len(results) - 1 else "🔵")
                rprint(f"  {marker} {t}")
        else:
            rprint("  [yellow]未找到匹配[/yellow]")

    cm.close()
    rprint("\n[bold green]✓ 演示完成[/bold green]")


@app.command()
def mcp(
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """启动 MCP 协议服务器（stdio 模式，供 Hermes 按需调用）"""
    _run_mcp_stdio(db)


@app.command()
def serve(
    socket_path: str = typer.Option("/tmp/chainmem.sock", "--socket", "-s",
                                    help="Unix socket 路径"),
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """启动持久化 MCP 服务（Unix socket，供 Hermes 常驻连接）

    模型在启动时一次性加载，之后查询毫秒级响应。
    用 systemd 管理此服务。
    """
    import os
    import asyncio
    import json

    # 预加载模型和索引（冷启动，仅一次）
    console.print("[bold]🔄 正在加载嵌入模型...[/bold]")
    cm = _get_cm(db)
    cm.retriever.rebuild_index()
    console.print(f"[bold green]✓ 模型就绪！[/bold green] {cm.stats()['nodes']} 个节点已索引")
    cm.close()

    # 确保 socket 目录存在
    os.makedirs(os.path.dirname(socket_path), exist_ok=True)
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    async def handle_connection(reader: asyncio.StreamReader,
                                writer: asyncio.StreamWriter):
        """处理一个连接：读取 JSON-RPC，处理后返回"""
        cm_conn = _get_cm(db)  # 轻量连接（不加载模型，复用已缓存的嵌入）
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip().decode("utf-8")
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    await _handle_mcp_request(req, cm_conn, writer)
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        finally:
            cm_conn.close()
            writer.close()

    async def server_main():
        server = await asyncio.start_unix_server(handle_connection, path=socket_path)
        os.chmod(socket_path, 0o666)  # 多用户可访问
        addr = server.sockets[0].getsockname()
        console.print(f"[bold green]✅ ChainMem MCP 服务启动！[/bold green]")
        console.print(f"  socket: [bold]{socket_path}[/bold]")
        console.print(f"  模型: all-MiniLM-L6-v2 (已加载)")
        console.print(f"  数据库: {db}")
        async with server:
            await server.serve_forever()

    asyncio.run(server_main())


# ── 安全命令 ─────────────────────────────────────

secure_app = typer.Typer(help="加密密钥管理")
app.add_typer(secure_app, name="secure")


@secure_app.command("init")
def secure_init(
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """初始化加密密钥（生成并保存到 ~/.chainmem/secret.key）
    
    首次运行生成密钥，之后可用 chainmem secure status 查看状态。
    也可设置 CHAINMEM_KEY 环境变量覆盖文件密钥。
    """
    from chainmem.core.crypto import generate_key, save_key_to_file, KEY_FILE

    key_path = Path(KEY_FILE)
    if key_path.exists():
        rprint(Panel(
            f"[yellow]⚠ 密钥文件已存在: {KEY_FILE}[/yellow]\n"
            f"如需重新生成，请先删除：rm {KEY_FILE}\n"
            f"警告：重新生成会无法解密之前加密的节点！",
            title="ChainMem Secure",
        ))
        raise typer.Exit(1)

    key = generate_key()
    path = save_key_to_file(key)
    rprint(Panel(
        f"[bold green]✓ 密钥已生成[/bold green]\n\n"
        f"文件: {path}\n"
        f"长度: 44 字符（Fernet 标准密钥）\n\n"
        f"[bold]也可设置环境变量覆盖：[/bold]\n"
        f"  export CHAINMEM_KEY={key}\n\n"
        f"[dim]提示：将上述 export 加入 ~/.bashrc 以便每次自动加载[/dim]",
        title="ChainMem Secure",
    ))


@secure_app.command("status")
def secure_status(
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """查看加密状态：密钥是否配置、加密节点统计"""
    from chainmem.core.crypto import KEY_FILE, ENV_VAR

    key_env = os.environ.get(ENV_VAR)
    key_file = Path(KEY_FILE)

    # 密钥状态
    env_ok = "✅ 已设置" if key_env else "❌ 未设置"
    file_ok = "✅ 已存在" if key_file.exists() else "❌ 不存在"

    # 统计加密节点
    cm = _get_cm(db)
    rows = cm.store.conn.execute(
        "SELECT COUNT(*) as total, SUM(encrypted) as encrypted FROM nodes"
    ).fetchone()
    total_nodes = rows["total"] or 0
    encrypted_count = rows["encrypted"] or 0
    cm.close()

    table = Table(title="ChainMem 加密状态")
    table.add_column("项目", style="cyan")
    table.add_column("状态", style="green")
    table.add_row("CHAINMEM_KEY 环境变量", env_ok)
    table.add_row(f"密钥文件 ({KEY_FILE})", file_ok)
    table.add_row("数据库", db)
    table.add_row("节点总数", str(total_nodes))
    table.add_row("加密节点数", f"[bold]{encrypted_count}[/bold]")
    if total_nodes > 0:
        pct = encrypted_count / total_nodes * 100
        table.add_row("加密率", f"{pct:.1f}%")
    console.print(table)

    if not key_env and not key_file.exists():
        rprint("\n[yellow]💡 运行 chainmem secure init 生成密钥[/yellow]")


@secure_app.command("encrypt")
def secure_encrypt(
    node_id: str = typer.Argument(..., help="要加密的节点 ID"),
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
):
    """手动加密指定节点（强制加密，不检查凭证模式）"""
    from chainmem.core.crypto import Encryptor

    encryptor = Encryptor()
    if not encryptor.available:
        rprint("[red]❌ 加密器未就绪，请先运行 chainmem secure init[/red]")
        raise typer.Exit(1)

    cm = _get_cm(db)
    node = cm.store.get_node(node_id)
    if node is None:
        rprint(f"[red]❌ 节点 {node_id} 不存在[/red]")
        raise typer.Exit(1)

    if node.get("encrypted"):
        rprint(f"[yellow]⚠ 节点 {node_id} 已加密，跳过[/yellow]")
        cm.close()
        return

    plaintext = node["text"]
    ciphertext, iv = encryptor.encrypt(plaintext)
    cm.store.conn.execute(
        "UPDATE nodes SET text = ?, encrypted = 1, encryption_iv = ?, text_prefix = '🔒' WHERE id = ?",
        (ciphertext, iv, node_id),
    )
    cm.store.conn.commit()
    cm.close()

    rprint(f"[bold green]✓ 节点 {node_id[:8]}... 已加密[/bold green]")


@secure_app.command("decrypt")
def secure_decrypt(
    node_id: str = typer.Argument(..., help="要解密的节点 ID"),
    db: str = typer.Option(DEFAULT_DB, "--db", "-d", help="数据库路径"),
    show: bool = typer.Option(False, "--show", "-s", help="显示解密后的原文"),
):
    """手动解密指定节点"""
    from chainmem.core.crypto import Encryptor

    encryptor = Encryptor()
    if not encryptor.available:
        rprint("[red]❌ 加密器未就绪[/red]")
        raise typer.Exit(1)

    cm = _get_cm(db)
    node = cm.store.get_node(node_id)
    if node is None:
        rprint(f"[red]❌ 节点 {node_id} 不存在[/red]")
        raise typer.Exit(1)

    if not node.get("encrypted"):
        rprint(f"[yellow]⚠ 节点 {node_id} 未加密，无需解密[/yellow]")
        cm.close()
        return

    plaintext = encryptor.decrypt(node["text"], node.get("encryption_iv", ""))
    cm.store.conn.execute(
        "UPDATE nodes SET text = ?, encrypted = 0, encryption_iv = '', text_prefix = ? WHERE id = ?",
        (plaintext, plaintext[:3], node_id),
    )
    cm.store.conn.commit()

    result = f"[bold green]✓ 节点 {node_id[:8]}... 已解密[/bold green]"
    if show:
        result += f"\n\n[bold]原文:[/bold]\n{plaintext}"
    rprint(result)
    cm.close()


# ── MCP 共享逻辑 ──

def _run_mcp_stdio(db: str):
    """stdio MCP 模式：从 stdin 读请求、stdout 写响应（Hermes 按需调用）"""
    import sys
    import json

    _cm_instance = None

    def get_cm():
        nonlocal _cm_instance
        if _cm_instance is None:
            _cm_instance = _get_cm(db)
        return _cm_instance

    def send_response(id, result):
        msg = json.dumps({"jsonrpc": "2.0", "id": id, "result": result})
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    def send_error(id, code, message):
        msg = json.dumps({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            _process_mcp_request(req, get_cm, send_response, send_error)
        except json.JSONDecodeError:
            pass
        except Exception:
            send_error(None, -1, traceback.format_exc())


async def _handle_mcp_request(req: dict, cm, writer: asyncio.StreamWriter,
                              rebuild_index: bool = True):
    """异步版 MCP 请求处理（serve 模式用）"""
    import json

    def send_response(id, result):
        msg = json.dumps({"jsonrpc": "2.0", "id": id, "result": result})
        writer.write((msg + "\n").encode("utf-8"))

    def send_error(id, code, message):
        msg = json.dumps({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
        writer.write((msg + "\n").encode("utf-8"))

    _process_mcp_request(req, lambda: cm, send_response, send_error,
                         rebuild_index=rebuild_index)
    await writer.drain()


def _process_mcp_request(req: dict, get_cm, send_response, send_error,
                         rebuild_index: bool = True):
    """MCP 请求处理核心（stdio 和 serve 模式共用）

    rebuild_index: True 则在每次 retrieve 前重建索引（stdio 模式），
                   False 则仅 ingest 后重建（serve 模式，索引常驻）
    """
    import json
    req_id = req.get("id")
    method = req.get("method")

    if method == "tools/list":
        send_response(req_id, {
            "tools": [
                {
                    "name": "chainmem_ingest",
                    "description": "结链：将文本存储为链式记忆",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "要结链的文本"},
                            "source": {"type": "string", "description": "来源会话"},
                            "tags": {"type": "string", "description": "标签（逗号分隔）"},
                        },
                        "required": ["text"],
                    },
                },
                {
                    "name": "chainmem_retrieve",
                    "description": "追溯：输入查询，还原完整记忆链（支持可选标签过滤）",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "查询文本"},
                            "tags": {"type": "string",
                                      "description": "可选，标签过滤（逗号分隔，OR 逻辑）"},
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "chainmem_stats",
                    "description": "查看记忆统计",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                    },
                },
            ]
        })

    elif method == "tools/call":
        tool_name = req.get("params", {}).get("name")
        arguments = req.get("params", {}).get("arguments", {})

        if tool_name == "chainmem_ingest":
            text = arguments.get("text", "")
            source = arguments.get("source", "")
            tags = [t.strip() for t in arguments.get("tags", "").split(",") if t.strip()]
            try:
                cm = get_cm()
                chain = cm.ingest(text, source=source, tags=tags)
                cm.retriever.rebuild_index()
                send_response(req_id, {
                    "content": [{"type": "text",
                                 "text": f"结链成功：{chain.node_count} 个节点，前缀「{chain.anchor_prefix}」"}]
                })
            except Exception as e:
                send_error(req_id, -1, str(e))

        elif tool_name == "chainmem_retrieve":
            query = arguments.get("query", "")
            tags_str = arguments.get("tags", "")
            tag_list = [t.strip() for t in tags_str.split(",") if t.strip()]
            cm = get_cm()
            if rebuild_index:
                cm.retriever.rebuild_index()
            results = cm.retrieve(query, tags=tag_list or None)
            if results:
                full_text = "".join(results)
                send_response(req_id, {
                    "content": [{"type": "text", "text": full_text}]
                })
            else:
                send_response(req_id, {
                    "content": [{"type": "text", "text": "未找到匹配的记忆"}]
                })

        elif tool_name == "chainmem_stats":
            cm = get_cm()
            stats = cm.stats()
            text = f"链总数: {stats['chains']}\n节点总数: {stats['nodes']}\n数据库: {stats['db_path']}"
            send_response(req_id, {
                "content": [{"type": "text", "text": text}]
            })

        else:
            send_error(req_id, -32601, f"未知工具: {tool_name}")

    elif method == "initialize":
        send_response(req_id, {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "chainmem", "version": "0.1.0"},
        })

    elif method == "notifications/initialized":
        pass

    else:
        send_error(req_id, -32601, f"未知方法: {method}")


if __name__ == "__main__":
    app()
