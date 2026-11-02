#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智鲸 AI · 桌面壳：把后端和前端装进一个原生窗口，不再依赖浏览器。

为什么要它
    之前的客户端是「起一个本地服务 + 调默认浏览器打开」，用户看到的是
    一个带地址栏、标签页、收藏夹的浏览器页面 —— 不像软件，也容易被误关。
    这里改成 pywebview + WebView2：一个没有浏览器界面的独立窗口，
    任务栏里就是「智鲸 AI」，关窗即退出，服务随之结束。

降级链（任何一环失败都不至于打不开）
    1. pywebview + WebView2  → 原生窗口（首选）
    2. Edge `--app=`          → 无地址栏的 Chromium 窗口（没有 WebView2 时）
    3. 默认浏览器             → 最后兜底

用法
    python tools/desktop_app.py                    # 起服务 + 开窗口
    python tools/desktop_app.py --port 8123
    python tools/desktop_app.py --url http://127.0.0.1:8000 --no-server   # 只开窗口
    python tools/desktop_app.py --no-window         # 只起服务（排查用）
"""

from __future__ import annotations

import argparse

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
ICON = ROOT / "app.ico"

APP_TITLE = "智鲸 AI"
WIN_W, WIN_H = 1360, 880


# ---------------------------------------------------------------------------
#  环境探测
# ---------------------------------------------------------------------------
def free_port(start: int = 8000, span: int = 20) -> int:
    for port in range(start, start + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"{start}–{start + span - 1} 端口都被占用，请用 --port 指定")


def http_ok(url: str, timeout: float = 2.0) -> bool:
    """探活。必须绕开系统代理：本机代理配置有误时回环请求也会被送进代理。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except (urllib.error.URLError, OSError):
        return False


def wait_health(base: str, seconds: float = 90) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if http_ok(f"{base}api/health"):
            return True
        time.sleep(0.4)
    return False


def has_webview2() -> bool:
    """WebView2 运行时是否装了（没装的话 pywebview 会退回 IE，Vue 直接白屏）。"""
    if not sys.platform.startswith("win"):
        return True
    try:
        import winreg
    except ImportError:
        return False
    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base and (Path(base) / "Microsoft" / "EdgeWebView" / "Application").is_dir():
            return True
    return False


def find_edge() -> Path | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if not base:
            continue
        for rel in ("Microsoft/Edge/Application/msedge.exe",):
            cand = Path(base) / rel
            if cand.is_file():
                return cand
    return None


# ---------------------------------------------------------------------------
#  后端
# ---------------------------------------------------------------------------
class Server:
    """把 uvicorn 作为子进程跑，窗口关掉时能干净地收掉。"""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self.base = f"http://{host}:{port}/"
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        env = os.environ.copy()
        env["SERVE_FRONTEND_DIR"] = str(FRONTEND_DIST)
        env["PYTHONIOENCODING"] = "utf-8"
        # 不缓冲输出：崩溃时能立刻在控制台/日志里看到
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", self.host, "--port", str(self.port),
                "--log-level", "warning",
            ],
            cwd=str(BACKEND),
            env=env,
        )

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass


# ---------------------------------------------------------------------------
#  窗口
# ---------------------------------------------------------------------------
def apply_icon(window_title: str, ico: Path) -> None:
    """给窗口套上应用图标。

    pywebview 没有跨平台的 icon 参数（Windows 上只能靠 exe 自带图标），
    而这里是用 python.exe 启动的，任务栏默认是 Python 的图标。用 WM_SETICON
    直接塞一个 HICON 进去，比改 exe 省事。
    """
    if not sys.platform.startswith("win") or not ico.is_file():
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    hwnd = user32.FindWindowW(None, window_title)
    if not hwnd:
        return
    IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
    for size, which in ((32, 1), (16, 0)):  # ICON_BIG=1, ICON_SMALL=0
        hicon = user32.LoadImageW(None, str(ico), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if hicon:
            user32.SendMessageW(hwnd, 0x0080, which, hicon)  # WM_SETICON


def run_window(url: str, debug: bool = False) -> bool:
    """开原生窗口。返回 True 表示窗口正常走完了生命周期。"""
    try:
        import webview
    except ImportError:
        return False

    window = webview.create_window(
        APP_TITLE,
        url,
        width=WIN_W,
        height=WIN_H,
        min_size=(1024, 680),
        text_select=True,
    )

    # 窗口出来后再补图标：FindWindow 要窗口已经创建
    def later() -> None:
        time.sleep(1.5)
        apply_icon(APP_TITLE, ICON)

    import threading

    threading.Thread(target=later, daemon=True).start()

    try:
        webview.start(debug=debug)
        return True
    except Exception as exc:  # noqa: BLE001  WebView2 缺失 / 初始化失败
        print(f"  [warn] 原生窗口起不来：{exc}", file=sys.stderr)
        return False


def run_edge_app(url: str) -> bool:
    """没有 WebView2 时的退路：Edge 的应用模式（没有地址栏和标签页）。"""
    edge = find_edge()
    if not edge:
        return False
    try:
        proc = subprocess.Popen([str(edge), f"--app={url}", f"--window-size={WIN_W},{WIN_H}"])
    except OSError:
        return False
    proc.wait()
    return True


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="智鲸 AI 桌面客户端")
    ap.add_argument("--port", type=int, default=0, help="后端端口，默认自动挑 8000 起的空位")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--url", default="", help="已有服务地址（配合 --no-server）")
    ap.add_argument("--no-server", action="store_true", help="不启动后端，只开窗口")
    ap.add_argument("--no-window", action="store_true", help="只启动后端，不开窗口")
    ap.add_argument("--browser", action="store_true", help="强制用默认浏览器打开")
    ap.add_argument("--debug", action="store_true", help="WebView 调试模式（右键检查元素）")
    args = ap.parse_args()

    server: Server | None = None
    if args.url:
        base = args.url if args.url.endswith("/") else args.url + "/"
    else:
        port = args.port or free_port()
        server = Server(port, args.host)
        base = server.base

    if not args.no_server:
        if not FRONTEND_DIST.is_dir():
            print(f"  [error] 找不到前端产物：{FRONTEND_DIST}", file=sys.stderr)
            return 2
        print(f"  正在启动服务 {base} ...")
        assert server is not None
        server.start()
        if not wait_health(base):
            print("  [error] 后端 90 秒内没有就绪，请查看上方日志", file=sys.stderr)
            server.stop()
            return 3

    if args.no_window:
        print(f"  服务已就绪：{base}（Ctrl+C 退出）")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if server:
                server.stop()
        return 0

    # ── 选一条能出窗口的路 ──────────────────────────────────
    ok = False
    if not args.browser:
        if has_webview2():
            ok = run_window(base, args.debug)
        else:
            print("  [info] 未检测到 WebView2 运行时，改用 Edge 应用模式")
        if not ok:
            ok = run_edge_app(base)
    if not ok:
        print("  [info] 退回到默认浏览器")
        webbrowser.open(base)

    if server:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
