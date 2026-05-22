#!/usr/bin/env python3
"""
ChainMem MCP stdio bridge — 可靠的 stdio ↔ Unix socket 代理。
比 socat 更稳定：正确行缓冲、连接重试、优雅关闭。
"""
import json, os, socket, sys, time

SOCKET_PATH = "/tmp/chainmem.sock"
MAX_RETRIES = 5
RETRY_DELAY = 0.5


def connect():
    for i in range(1, MAX_RETRIES + 1):
        if os.path.exists(SOCKET_PATH):
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(30)
                s.connect(SOCKET_PATH)
                return s
            except (ConnectionRefusedError, OSError) as e:
                print(f"[chainmem] 连接失败 ({i}/{MAX_RETRIES}): {e}", file=sys.stderr, flush=True)
                try: s.close()
                except: pass
        else:
            print(f"[chainmem] {SOCKET_PATH} 不存在 ({i}/{MAX_RETRIES})", file=sys.stderr, flush=True)
        time.sleep(RETRY_DELAY)
    return None


def recv_line(sock):
    """从 socket 读取一行（直到 \\n）"""
    buf = b""
    while True:
        try:
            b = sock.recv(1)
        except socket.timeout:
            return None
        if not b:
            return None
        buf += b
        if b == b"\n":
            return buf.decode().strip()


sock = connect()
if not sock:
    print(f"[chainmem] 无法连接 {SOCKET_PATH}，请确认 chainmem 服务已运行", file=sys.stderr, flush=True)
    sys.exit(1)

print(f"[chainmem] 已连接 {SOCKET_PATH}", file=sys.stderr, flush=True)

try:
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue

        # stdout → socket
        try:
            sock.sendall((line + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            print(f"[chainmem] socket 断开", file=sys.stderr, flush=True)
            break

        # 读取响应 — 注意：notifications/initialized 没有响应
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        if method == "notifications/initialized":
            # 通知类不等待响应
            continue

        resp = recv_line(sock)
        if resp is None:
            print(f"[chainmem] 读取响应超时或断开", file=sys.stderr, flush=True)
            break

        print(resp, flush=True)

except (EOFError, KeyboardInterrupt):
    pass
finally:
    try:
        sock.close()
    except Exception:
        pass
    print(f"[chainmem] 连接关闭", file=sys.stderr, flush=True)
