#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智鲸 AI · 桌面壳：把后端和前端装进一个原生窗口，不再依赖浏览器。

为什么要它
    之前的客户端是「起一个本地服务 + 调默认浏览器打开」，用户看到的是
    一个带地址栏、标签页、收藏夹的浏览器页面 —— 不像软件，也容易被误关。
    这里改成 pywebview + WebView2：一个没有浏览器界面的独立窗口，
    任务栏里就是「智鲸 AI」。

关窗 ≠ 退出：
    点右上角 X 只是把窗口藏起来，后台服务继续跑，托盘里留一个图标。
    要真正退出，右键托盘图标 → 退出（或窗口里的「退出」菜单）。
    这样误点关闭不会丢会话，重新打开还是原来那个。
    想恢复「关窗即退出」的老行为：加 `--close-quits`。

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
import threading
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
def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """端口是否被占用。

    ★ 不能只用 bind 判断：Windows 上 SO_REUSEADDR 允许两个 socket 绑同一端口，
      被占用的端口照样 bind 得成功（Linux 不会）。所以先 connect 探活：
      连得上 = 有人在监听 = 不可用。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex((host, port)) == 0:
            return True
    # 没人监听，再确认能独占绑定（不带 SO_REUSEADDR，避免误判）
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        try:
            holder.bind((host, port))
            return False
        except OSError:
            return True


def free_port(start: int = 8000, span: int = 20) -> int:
    for port in range(start, start + span):
        if not port_in_use(port):
            return port
    raise SystemExit(
        f"端口 {start}–{start + span - 1} 全被占用。\n"
        f"可以用 --port 指定其它端口，例如：start.cmd -Port 9000"
    )


def show_error(title: str, text: str) -> None:
    """弹一个系统对话框。

    pythonw 没有控制台，报错只写进日志文件 —— 对用户来说等同于「双击没反应」。
    """
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001 - 弹不出就算了，日志里还有
        pass


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
        self._log = None

    def start(self) -> None:
        env = os.environ.copy()
        env["SERVE_FRONTEND_DIR"] = str(FRONTEND_DIST)
        env["PYTHONIOENCODING"] = "utf-8"
        # 不缓冲输出：崩溃时能立刻在控制台/日志里看到
        env["PYTHONUNBUFFERED"] = "1"
        # ★ 子进程的输出必须显式给个文件：
        #   pythonw 起的父进程没有 stdout/stderr，uvicorn 的日志处理器
        #   拿到 None 会在启动阶段就崩，用户看到的是「窗口等不到后端」。
        try:
            log_dir = BACKEND / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log = open(log_dir / "backend.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        except OSError:
            self._log = subprocess.DEVNULL

        self.proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", self.host, "--port", str(self.port),
                "--log-level", "warning",
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=0x08000000 if os.name == "nt" else 0,  # CREATE_NO_WINDOW
        )

    def last_error(self) -> str:
        """从后端日志尾部捞一条能解释失败原因的行。

        端口冲突时 uvicorn 会写 `[Errno 10048] ... bind`，但进程随即退出，
        父进程只看到「没就绪」。把这一行找出来，用户才知道该干什么。
        """
        path = BACKEND / "logs" / "backend.log"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
        except OSError:
            return ""
        marks = ("10048", "already in use", "Address already", "ERROR", "Error", "Traceback")
        for line in reversed(lines):
            if any(mark in line for mark in marks):
                return line.strip()[:300]
        return ""

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        log = getattr(self, "_log", None)
        if proc and proc.poll() is None:
            pass
        elif log not in (None, subprocess.DEVNULL):
            try:
                log.close()
            except OSError:
                pass
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
#  任务栏身份 + 窗口钩子 + 托盘
# ---------------------------------------------------------------------------
#: 任务栏分组用的应用标识。Windows 默认按 exe 分组，python.exe 的图标就跟着出来了；
#: 显式给一个 AppUserModelID，任务栏才会把我们当成独立应用、用我们自己的图标。
APP_USER_MODEL_ID = "ZhijingAI.Client.1"

WM_CLOSE = 0x0010
GWLP_WNDPROC = -4
SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9


#: 单实例互斥体句柄。必须留引用，否则会被 GC 关掉，互斥就失效了。
_INSTANCE_MUTEX = None


def ensure_single_instance() -> bool:
    """确保只有一个实例在跑。返回 False 表示「已有一个在运行」。"""
    global _INSTANCE_MUTEX
    if not sys.platform.startswith("win"):
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        _INSTANCE_MUTEX = kernel32.CreateMutexW(
            None, False, "Local\\ZhijingAIClient.SingleInstance"
        )
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() != ERROR_ALREADY_EXISTS:
            return True
    except Exception:  # noqa: BLE001 - 拿不到互斥体时宁可放行，也不要打不开
        return True

    # 已有实例：把它的窗口叫到前台，让用户看到「已经开着了」
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW(None, APP_TITLE)
        if hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass
    return False


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """让任务栏把本进程当成独立应用（图标、分组、跳转列表都用我们的）。"""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:  # noqa: BLE001 - 老系统没有这个 API，不影响运行
        pass


class WindowHook:
    """钩住主窗口的窗口过程，把「关闭」改成「隐藏」。

    ★ 为什么不用 pywebview 的 window.events.closing：
      这个事件只在 cocoa / gtk / qt / winforms 平台接线，**WebView2（edgechromium）
      平台没有接**，注册了也不会被调用。唯一可靠的入口是 Win32 的 WM_CLOSE。
    """

    def __init__(self, title: str, timeout: float = 15.0) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._user32 = ctypes.windll.user32
        self.allow_close = False
        self.on_hide = None      # 隐藏后的回调（第一次提示「在后台运行」）
        self.hwnd = 0

        user32 = self._user32
        user32.FindWindowW.restype = wintypes.HWND
        deadline = time.time() + timeout
        while time.time() < deadline:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                self.hwnd = hwnd
                break
            time.sleep(0.3)
        if not self.hwnd:
            raise RuntimeError("找不到主窗口，无法接管关闭行为")

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,           # LRESULT
            wintypes.HWND,
            ctypes.c_uint,              # UINT msg
            ctypes.c_size_t,            # WPARAM
            ctypes.c_ssize_t,           # LPARAM
        )
        self._proc = WNDPROC(self._wndproc)   # ★ 必须留引用，否则回调被回收 → 崩溃
        setter = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        setter.restype = ctypes.c_void_p
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        self._old = setter(self.hwnd, GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p))

        self._call = user32.CallWindowProcW
        self._call.restype = ctypes.c_ssize_t
        self._call.argtypes = [
            ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_ssize_t,
        ]

    # 窗口过程本身：只拦 WM_CLOSE，其余原样交给系统的默认处理
    def _wndproc(self, hwnd, msg, wparam, lparam):  # noqa: ANN001
        if msg == WM_CLOSE and not self.allow_close:
            self._user32.ShowWindow(hwnd, SW_HIDE)
            if self.on_hide:
                try:
                    self.on_hide()
                except Exception:  # noqa: BLE001
                    pass
            return 0
        return self._call(self._old, hwnd, msg, wparam, lparam)

    def hide(self) -> None:
        if self.hwnd:
            self._user32.ShowWindow(self.hwnd, SW_HIDE)

    def show(self) -> None:
        if not self.hwnd:
            return
        self._user32.ShowWindow(self.hwnd, SW_SHOW)
        self._user32.ShowWindow(self.hwnd, SW_RESTORE)
        try:
            self._user32.SetForegroundWindow(self.hwnd)
        except Exception:  # noqa: BLE001
            pass

    def close_for_real(self) -> None:
        """真的关窗：放行 WM_CLOSE，让 pywebview 正常走销毁流程。"""
        self.allow_close = True
        if self.hwnd:
            self._user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


def _load_tray_image(ico: Path):
    """托盘图标：优先用应用自己的 ico，取不到就画一个蓝色圆点兜底。"""
    try:
        from PIL import Image

        if ico.is_file():
            img = Image.open(ico)
            img.size = (256, 256)          # .ico 里挑最大的一帧
            return img.convert("RGBA")
    except Exception:  # noqa: BLE001
        pass
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((4, 4, 60, 60), fill=(26, 74, 122, 255))
    return img


class Tray:
    """系统托盘图标。

    没有 pystray 时降级为「无托盘」：窗口照常，但关窗就是关窗（不做隐藏），
    否则用户会找不到窗口 —— 藏起来的程序没法再叫出来比直接退出更糟。
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self._icon = None
        self._thread = None

    def start(self, hook: WindowHook) -> bool:
        try:
            import pystray
        except ImportError:
            return False

        def show_window(icon=None, item=None) -> None:
            hook.show()

        def open_browser(icon=None, item=None) -> None:
            webbrowser.open(self.base_url)

        def quit_app(icon=None, item=None) -> None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass
            hook.close_for_real()

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", show_window, default=True),
            pystray.MenuItem("在浏览器中打开", open_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", quit_app),
        )
        self._icon = pystray.Icon(
            "zhijing_ai", _load_tray_image(ICON), f"{APP_TITLE}（在后台运行）", menu
        )
        self._thread = threading.Thread(
            target=self._icon.run, name="zhijing-tray", daemon=True
        )
        self._thread.start()
        return True

    def notify_hidden(self) -> None:
        """第一次隐藏时提示一句，避免用户以为程序被关掉了。"""
        if not self._icon:
            return
        try:
            self._icon.notify("程序仍在后台运行，点托盘图标可以重新打开", APP_TITLE)
        except Exception:  # noqa: BLE001 - 通知不可用不影响主流程
            pass

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self._icon = None


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


def _selftest_flow(hook: "WindowHook", state: dict) -> None:
    """关窗 → 应该隐藏且进程还活着；托盘唤回 → 应该重新可见；最后真退出。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = ctypes.c_bool

    problems: list[str] = []
    hwnd = hook.hwnd

    time.sleep(1.0)
    if not user32.IsWindowVisible(hwnd):
        problems.append("窗口一开始就不可见")
    else:
        print("  [ok] 窗口已显示")

    print(f"  [ok] 托盘图标：{'已创建' if state.get('tray') else '未创建'}")

    # 任务栏身份：AUMID 决定分组与图标来源，没设的话任务栏会显示 python.exe 的图标
    shell32 = ctypes.windll.shell32
    aumid = ctypes.c_wchar_p()
    hr = shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(aumid))
    if hr == 0 and aumid.value:
        print(f"  [ok] 任务栏应用标识：{aumid.value}")
    else:
        problems.append("未设置 AppUserModelID，任务栏图标会退回 python.exe")

    # 窗口图标（WM_GETICON：ICON_BIG=1 / ICON_SMALL=0）
    user32.SendMessageW.restype = ctypes.c_ssize_t
    big = user32.SendMessageW(hwnd, 0x007F, 1, 0)
    small = user32.SendMessageW(hwnd, 0x007F, 0, 0)
    if big or small:
        print(f"  [ok] 窗口图标已设置（大图标={'有' if big else '无'}，小图标={'有' if small else '无'}）")
    else:
        problems.append("窗口没有拿到应用图标")

    # 点 X
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.5)
    if user32.IsWindowVisible(hwnd):
        problems.append("点 X 之后窗口仍然可见（拦截没生效）")
    else:
        print("  [ok] 点 X 后窗口已隐藏（进程未退出）")

    # 托盘「显示主窗口」
    hook.show()
    time.sleep(0.8)
    if not user32.IsWindowVisible(hwnd):
        problems.append("托盘唤回后窗口没有出现")
    else:
        print("  [ok] 托盘唤回后窗口重新可见")

    # 再藏一次，确认可以反复
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    time.sleep(1.0)
    if user32.IsWindowVisible(hwnd):
        problems.append("第二次点 X 没有隐藏")
    else:
        print("  [ok] 第二次点 X 仍然隐藏")

    state["selftest_problems"] = problems
    # 退出：放行 WM_CLOSE，让 pywebview 正常收尾
    hook.close_for_real()


def run_window(
    url: str,
    debug: bool = False,
    close_quits: bool = False,
    selftest: bool = False,
) -> bool:
    """开原生窗口。返回 True 表示窗口走完了完整生命周期。

    close_quits=False（默认）：点 X = 藏到托盘，服务继续跑；
    close_quits=True：恢复「关窗即退出」的老行为。
    """
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

    state: dict = {"hook": None, "tray": None, "notified": False}

    def boot() -> None:
        """窗口一出现就接管：任务栏身份 → 图标 → 关闭拦截 → 托盘。"""
        set_app_user_model_id()
        try:
            hook = WindowHook(APP_TITLE)
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] 无法接管关闭行为，窗口将直接关闭：{exc}", file=sys.stderr)
            apply_icon(APP_TITLE, ICON)
            return

        if close_quits:
            hook.allow_close = True
        else:
            tray = Tray(url)
            if tray.start(hook):
                state["tray"] = tray
                state["hook"] = hook

                def first_hide() -> None:
                    if not state["notified"]:
                        state["notified"] = True
                        tray.notify_hidden()

                hook.on_hide = first_hide
            else:
                # 没有托盘就别藏：藏起来叫不出来，比直接退出更糟
                hook.allow_close = True
                print("  [warn] 托盘不可用（缺 pystray），关闭窗口将直接退出", file=sys.stderr)

        apply_icon(APP_TITLE, ICON)

        if selftest:
            threading.Thread(
                target=_selftest_flow, args=(hook, state), daemon=True
            ).start()

    threading.Thread(target=boot, daemon=True).start()

    try:
        webview.start(debug=debug)
        if selftest:
            # ★ 不能用 `or`：空列表是「全部通过」，但 falsy 会被误判成没跑完
            problems = (
                state["selftest_problems"]
                if "selftest_problems" in state
                else ["自检没有跑完"]
            )
            if problems:
                for msg in problems:
                    print(f"  [fail] {msg}", file=sys.stderr)
                return False
            print("  [ok] 自检全部通过")
        return True
    except Exception as exc:  # noqa: BLE001  WebView2 缺失 / 初始化失败
        print(f"  [warn] 原生窗口起不来：{exc}", file=sys.stderr)
        return False
    finally:
        if state["tray"]:
            state["tray"].stop()


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
def _ensure_streams() -> None:
    """pythonw.exe 下 sys.stdout / sys.stderr 是 None。

    uvicorn 的日志处理器和我们的 print 都会往这两个流写，None 会直接抛
    AttributeError —— 表现是「双击没反应」。这里把它们接到日志文件。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_dir = BACKEND / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "desktop.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main() -> int:
    _ensure_streams()

    if not ensure_single_instance():
        print(f"{APP_TITLE} 已经在运行，已切换到它的窗口。")
        return 0

    ap = argparse.ArgumentParser(description="智鲸 AI 桌面客户端")
    ap.add_argument("--port", type=int, default=0, help="后端端口，默认自动挑 8000 起的空位")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--url", default="", help="已有服务地址（配合 --no-server）")
    ap.add_argument("--no-server", action="store_true", help="不启动后端，只开窗口")
    ap.add_argument("--no-window", action="store_true", help="只启动后端，不开窗口")
    ap.add_argument("--browser", action="store_true", help="强制用默认浏览器打开")
    ap.add_argument("--debug", action="store_true", help="WebView 调试模式（右键检查元素）")
    ap.add_argument(
        "--selftest",
        action="store_true",
        help="自检：验证关窗缩托盘与托盘唤回（配合 --no-server --url about:blank）",
    )
    ap.add_argument(
        "--close-quits",
        action="store_true",
        help="点 X 直接退出（默认是隐藏到托盘继续运行）",
    )
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

        # 一边等健康、一边盯着子进程：它要是先退出了（端口被抢、依赖缺失），
        # 就没必要再等满 90 秒 —— 直接把真实原因抛出来。
        healthy = False
        deadline = time.time() + 90
        while time.time() < deadline:
            if http_ok(f"{base}api/health"):
                healthy = True
                break
            if server.proc is not None and server.proc.poll() is not None:
                break
            time.sleep(0.4)

        if not healthy:
            detail = server.last_error() or "后端未能就绪"
            hint = ""
            if "10048" in detail or "already in use" in detail.lower():
                hint = (
                    f"\n端口被占用：{base}\n"
                    f"排查：netstat -ano | findstr :{server.port}\n"
                    f"换端口：start.cmd -Port 9000"
                )
            # 只有 pythonw（无控制台）才弹框：有控制台时 stderr 已经能看到，
            # 弹个模态框反而挡住日志、也妨碍自动化测试。
            if sys.stdout is None:
                show_error("智鲸 AI 启动失败", f"{detail}{hint}")
            print(f"  [error] {detail}", file=sys.stderr)
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
            ok = run_window(
                base, args.debug, close_quits=args.close_quits, selftest=args.selftest
            )
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
