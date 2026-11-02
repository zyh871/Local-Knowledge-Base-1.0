#!/usr/bin/env python3
"""零依赖静态服务器（仅用标准库）。

用途：需要给页面一个真实 http:// 地址时（演示机、内网分发、演示投影），
不必装 nginx、不必装任何三方包。

    python serve.py                 # http://127.0.0.1:8080
    python serve.py --port 9000     # 换端口
    python serve.py --open          # 启动后自动打开浏览器
    python serve.py --host 0.0.0.0  # 允许局域网访问（默认只监听本机）

安全默认：默认只绑 127.0.0.1。改成 0.0.0.0 时会在启动日志里明确提示。
"""

from __future__ import annotations

import argparse
import http.server
import mimetypes
import os
import socket
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

#: 页面所在目录 = 本脚本的上一级（部署后 index.html 与 deploy/ 平级）
ROOT = Path(__file__).resolve().parent.parent

#: 单文件页面用不到这些，但补上能避免某些浏览器把 .html 当下载
mimetypes.add_type("text/html", ".html")
mimetypes.add_type("image/png", ".png")


class Handler(http.server.SimpleHTTPRequestHandler):
    """在 SimpleHTTPRequestHandler 上补三件事：正确的根目录、安全头、安静日志。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        # 单文件页面：不缓存，改了立刻生效
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # 页面自身没有任何外链脚本，可以放心收紧 CSP
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'",
        )
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 默认日志会把每个静态请求都刷屏，演示时反而看不清
        if args and str(args[1]) != "200":
            sys.stderr.write("  %s %s\n" % (self.requestline, args[1]))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def pick_port(host: str, preferred: int) -> int:
    """端口被占用时顺延，而不是直接报错退出。"""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                continue
    raise SystemExit(f"端口 {preferred}–{preferred + 19} 都被占用，请用 --port 指定其它端口")


def main() -> None:
    ap = argparse.ArgumentParser(description="智鲸 AI 产品介绍页 · 本地静态服务器")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    ap.add_argument("--port", type=int, default=8080, help="端口（默认 8080，占用时自动顺延）")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = ap.parse_args()

    index = ROOT / "index.html"
    if not index.exists():
        raise SystemExit(f"找不到页面文件：{index}\n请确认 serve.py 与 index.html 处于同一套目录结构中")

    port = pick_port(args.host, args.port)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{port}/"

    print("智鲸 AI · 产品介绍页")
    print(f"  页面目录  {ROOT}")
    print(f"  访问地址  {url}")
    if args.host == "0.0.0.0":
        print("  ⚠ 已监听全部网卡，同一局域网内的其他设备也能访问")
    print("  Ctrl+C 停止")
    print()

    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        with Server((args.host, port), Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    os.chdir(ROOT)  # 让相对路径行为与预期一致
    main()
