#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智鲸 AI 客户端 · 图形安装程序

双击运行 → 选择安装位置 → 安装 → 提示完成并可一键启动。

★ 为什么不用 IExpress 那套
    之前把同一份内容用 IExpress 包成自解压 exe，安装逻辑只能写在批处理里。
    IExpress 的宿主（wextract）跑在受限作业对象中，实测在它里面：
      * Expand-Archive / ZipFile 解压会中途整进程消失
      * 任何 Start-Process / 子进程会被连同宿主一起杀掉
      * 界面完全不可控（ShowInstallProgramWindow / FinishMessage 都验证不出可见窗口）
    结果就是用户双击后「毫无反应」。改成 PyInstaller 打包的窗口程序后，
    解压、写注册表、建启动项都在自己的进程里完成，进度和结果都看得见。

打包（由 deploy/build_client.py 调用）：
    pyinstaller --onefile --noconsole --icon app.ico \
        --add-data "payload.zip;." installer_gui.py
"""

from __future__ import annotations

import json
import os
import random
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.request
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "智鲸 AI 客户端"
APP_VERSION = "1.0.0"
#: 默认安装位置。用户可以在界面上改成任意目录（不需要管理员权限的目录）
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ZhijingAIClient"
LAUNCHER_NAME = "智鲸 AI 客户端.cmd"
LOG = Path(tempfile.gettempdir()) / "zhijing-client-install.log"

# 启动器模板：路径在安装时填入。cmd 按系统 ANSI 码页解析，所以写盘时用 GBK。
LAUNCHER_TMPL = """@echo off
setlocal
set "DIR={target}"
if not exist "%DIR%\\start.cmd" (
    echo Zhijing AI Client is not installed.
    del /q "%~f0"
    exit /b 1
)
cd /d "%DIR%"
call start.cmd %*
"""

UNINSTALLER_TMPL = """@echo off
chcp 936 >nul
setlocal
echo 正在卸载「智鲸 AI 客户端」...
rmdir /s /q "{target}"
del /q "%USERPROFILE%\\Desktop\\智鲸 AI 客户端.lnk" >nul 2>&1
del /q "%USERPROFILE%\\Desktop\\{launcher}" >nul 2>&1
del /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\智鲸 AI 客户端.lnk" >nul 2>&1
del /q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{launcher}" >nul 2>&1
reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\ZhijingAIClient" /f >nul 2>&1
echo 已卸载。
pause
"""


def log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


def bundle_dir() -> Path:
    """PyInstaller onefile 会把自己解到 _MEIPASS；源码运行时就是脚本目录。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def write_text(path: Path, text: str, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding, newline="\r\n")


# ---------------------------------------------------------------------------
#  首次启动配置
# ---------------------------------------------------------------------------
def probe_ollama() -> tuple[str, str, str | None] | None:
    """看一眼本机有没有可用的 Ollama 对话模型。

    返回 (base, 对话模型, 向量模型或 None)。有本地模型时生成的 .env 不含任何密钥，
    用户点「启动客户端」直接能聊。
    """
    base = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 回环绝不过代理
    try:
        with opener.open(f"{base}/api/tags", timeout=2.0) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    embed_hints = ("embed", "bge", "mxbai", "minilm", "gte", "e5-", "jina")
    names = [m.get("name", "") for m in (tags.get("models") or []) if m.get("name")]
    if not names:
        return None

    chats = [n for n in names if not any(k in n.lower() for k in embed_hints)]
    embeds = [n for n in names if any(k in n.lower() for k in embed_hints)]
    if not chats:
        return None
    pref = ("qwen", "glm", "deepseek", "yi", "llama", "gemma", "mistral")
    chats.sort(key=lambda n: -next((i for i, p in enumerate(pref) if p in n.lower()), 99))
    return base, chats[0], (embeds[0] if embeds else None)


def ensure_env(target: Path) -> tuple[str, str | None]:
    """生成 backend/.env。返回 (说明, 初始密码或 None)。"""
    env_file = target / "backend" / ".env"
    if env_file.exists():
        return "已存在配置，保留不动", None

    password = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(12))
    secret = "".join(random.choice("0123456789abcdef") for _ in range(64))
    lines = [
        "# 由安装程序生成，改完保存即可生效。",
        "ADMIN_USERNAME=admin",
        f"ADMIN_PASSWORD={password}",
        f"JWT_SECRET_KEY={secret}",
    ]

    local = probe_ollama()
    if local:
        base, model, embed = local
        lines += [
            "",
            "# 检测到本机 Ollama，默认用本地模型（数据不出本机，无需密钥）",
            "MODEL_BACKEND=ollama",
            f"OLLAMA_BASE_URL={base}",
            f"LLM_MODEL={model}",
        ]
        if embed:
            lines.append(f"EMBEDDING_MODEL={embed}")
            note = f"已检测到本地模型 {model}（向量模型 {embed}），启动后即可直接对话"
        else:
            # 没有向量模型时绝不能留空：默认值 nomic-embed-text 往往没装，
            # 而 Chroma 在嵌入不可用时会回落到内置 ONNX 模型，首次上传就要联网下 79 MB。
            lines += [
                "# 本机没有向量模型，检索用零依赖的 TF-IDF（不需要联网下载）",
                "EMBEDDING_MODEL=local-tfidf",
            ]
            note = f"已检测到本地模型 {model}，检索使用内置 TF-IDF（无需下载）"
    else:
        lines += [
            "",
            "# 没检测到本地模型。两条路选一条：",
            "#   1) 装 Ollama 后双击「检测本地模型.cmd」，自动切到本地模型",
            "#   2) 填云端密钥：把下面 ZHIPU_API_KEY 补全",
            "MODEL_BACKEND=zhipu",
            "ZHIPU_API_KEY=",
            "# 云端嵌入模型（智谱 embedding-3，2048 维）",
            "EMBEDDING_MODEL=embedding-3",
        ]
        note = "未检测到本地模型，需要用云端模型（启动前请填 ZHIPU_API_KEY）"

    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return note, password


# ---------------------------------------------------------------------------
#  安装
# ---------------------------------------------------------------------------
def check_target(target: Path) -> str | None:
    """返回错误说明；可用则返回 None。选目录时就先判，别等装到一半才报错。"""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return (
            f"没有写入权限：{target}\n{exc}\n\n"
            "C:\\Program Files 这类目录需要管理员权限，请换一个位置，"
            "例如 D:\\智鲸AI 或默认的 AppData 目录。"
        )
    return None


def create_shortcuts(target: Path) -> int:
    """在桌面与开始菜单创建**真正的 .lnk 快捷方式**（带图标、可改属性）。

    桌面直接放 .cmd 很难看：图标是批处理的黑窗口，右键属性也没法改。
    这里用 PowerShell 的 WScript.Shell 建 .lnk —— 安装器自己是正常进程，
    起子进程完全没问题（旧 IExpress 宿主里才会被连带杀掉）。

    脚本写成 UTF-8 + BOM 的临时 .ps1 再执行，避免中文塞进 `-Command`
    被控制台代码页搞乱。
    """
    icon = target / "app.ico"
    start = target / "start.cmd"
    ps = f'''$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$target = "{start}"
$work = "{target}"
$icon = "{icon}"
$made = 0
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
    if (-not (Test-Path $dir)) {{ continue }}
    $lnk = Join-Path $dir '智鲸 AI 客户端.lnk'
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $target
    $s.WorkingDirectory = $work
    $s.Description = '智鲸 AI 本地知识库客户端'
    if (Test-Path $icon) {{ $s.IconLocation = "$icon,0" }}
    $s.WindowStyle = 7
    $s.Save()
    $made++
}}
Write-Output $made
'''
    script = target / "_mklnk.ps1"
    script.write_text(ps, encoding="utf-8-sig")
    made = 0
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, timeout=60,
        )
        out = (proc.stdout or "").strip().splitlines()
        made = int(out[-1]) if out and out[-1].strip().isdigit() else 0
        log(f"lnk: rc={proc.returncode} made={made} err={(proc.stderr or '')[:200]}")
    except Exception as exc:  # noqa: BLE001
        log(f"lnk failed: {exc!r}")
    finally:
        script.unlink(missing_ok=True)

    if made:
        return made

    # 兜底：.lnk 建不出来时退回 .cmd，至少能启动
    fallback = LAUNCHER_TMPL.format(target=target)
    for folder in (
        Path(os.environ["USERPROFILE"]) / "Desktop",
        Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ):
        try:
            if folder.is_dir():
                write_text(folder / LAUNCHER_NAME, fallback, "gbk")
                made += 1
        except OSError as exc:
            log(f"shortcut fallback failed {folder}: {exc}")
    return made


def install(target: Path, progress) -> dict:
    payload = bundle_dir() / "payload.zip"
    if not payload.exists():
        raise RuntimeError("安装包不完整：找不到 payload.zip")

    progress(0.05, "准备目录...")
    # ★ 绝不能无条件 rmtree：用户可能把位置选在一个已有资料的目录里。
    #   只有「确认过是我们自己的旧安装」（有 VERSION.txt）才整体清空；
    #   否则只做覆盖式解压，不动别人的文件。
    #
    # ★ 卸载前先把 .env 捞出来。里面是管理员密码、JWT 密钥、模型配置——
    #   升级重装时丢掉它，用户就得重新记一遍新密码、重新配一遍模型。
    previous_env = None
    env_path = target / "backend" / ".env"
    if env_path.is_file():
        try:
            previous_env = env_path.read_bytes()
        except OSError as exc:
            log(f"read previous env failed: {exc}")

    if (target / "VERSION.txt").exists() and (target / "start.cmd").exists():
        log(f"wipe previous install: {target}")
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    progress(0.12, "解压文件...")
    with zipfile.ZipFile(payload) as zf:
        names = zf.namelist()
        total = len(names) or 1
        for i, name in enumerate(names):
            zf.extract(name, target)
            if i % 20 == 0:
                progress(0.12 + 0.66 * i / total, "解压文件...")
    if not (target / "start.cmd").exists():
        raise RuntimeError("解压后缺少 start.cmd，安装包可能损坏")

    if previous_env is not None:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_bytes(previous_env)
        log("restored previous .env")

    progress(0.80, "创建快捷方式...")
    made = create_shortcuts(target)

    progress(0.88, "写入卸载入口...")
    write_text(
        target / "卸载.cmd",
        UNINSTALLER_TMPL.format(target=target, launcher=LAUNCHER_NAME),
        "gbk",
    )
    try:
        import winreg

        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ZhijingAIClient",
        )
        values = (
            ("DisplayName", APP_NAME),
            ("DisplayVersion", APP_VERSION),
            ("Publisher", "智鲸 AI"),
            ("InstallLocation", str(target)),
            ("UninstallString", f'"{target}\\卸载.cmd"'),
        )
        for name, value in values:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
    except OSError as exc:
        log(f"registry failed: {exc}")

    progress(0.95, "生成配置...")
    note, password = ensure_env(target)

    progress(1.0, "完成")
    log(f"install ok: target={target} launchers={made}")
    return {"target": target, "note": note, "password": password, "launchers": made}


# ---------------------------------------------------------------------------
#  界面
# ---------------------------------------------------------------------------
class Installer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION} · 安装程序")
        self.geometry("560x340")
        self.resizable(False, False)
        self.configure(bg="#F7F9FC")
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        self.target = tk.StringVar(value=str(DEFAULT_DIR))

        tk.Label(
            self, text=f"安装 {APP_NAME}", font=("Microsoft YaHei UI", 16, "bold"),
            bg="#F7F9FC", fg="#1A2330",
        ).pack(pady=(20, 2))
        tk.Label(
            self, text="选择位置后点「开始安装」。不需要管理员权限，可随时卸载。",
            font=("Microsoft YaHei UI", 9), bg="#F7F9FC", fg="#6B7A90",
        ).pack()

        # ── 安装位置 ─────────────────────────────────────────
        row = tk.Frame(self, bg="#F7F9FC")
        row.pack(fill="x", padx=28, pady=(18, 4))
        tk.Label(
            row, text="安装位置", font=("Microsoft YaHei UI", 10), bg="#F7F9FC", fg="#1A2330",
        ).pack(side="left")
        self.entry = tk.Entry(
            row, textvariable=self.target, font=("Microsoft YaHei UI", 9),
            relief="solid", bd=1, bg="white",
        )
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 6), ipady=4)
        self.browse = tk.Button(
            row, text="浏览...", width=7, font=("Microsoft YaHei UI", 9), command=self.pick_dir,
        )
        self.browse.pack(side="left")

        tk.Label(
            self, text="可以装在任意有写入权限的目录，例如 D:\\智鲸AI。",
            font=("Microsoft YaHei UI", 8), bg="#F7F9FC", fg="#9AA7B8",
        ).pack(anchor="w", padx=28)

        # ── 进度 ────────────────────────────────────────────
        self.bar = ttk.Progressbar(self, length=500, mode="determinate", maximum=100)
        self.bar.pack(pady=(18, 6))
        self.status = tk.Label(
            self, text="", font=("Microsoft YaHei UI", 9), bg="#F7F9FC", fg="#1A4A7A",
        )
        self.status.pack()
        self.hint = tk.Label(
            self, text="", font=("Microsoft YaHei UI", 9), bg="#F7F9FC",
            fg="#6B7A90", wraplength=500, justify="left",
        )
        self.hint.pack(pady=(4, 0))

        self.buttons = tk.Frame(self, bg="#F7F9FC")
        self.buttons.pack(side="bottom", pady=14)
        self.btn_install = tk.Button(
            self.buttons, text="开始安装", width=12, height=1,
            font=("Microsoft YaHei UI", 10), bg="#1A4A7A", fg="white", relief="flat",
            command=self.start,
        )
        self.btn_install.pack(side="left", padx=6)
        self.btn_cancel = tk.Button(
            self.buttons, text="取消", width=8, font=("Microsoft YaHei UI", 10),
            command=self.destroy,
        )
        self.btn_cancel.pack(side="left", padx=6)

    # ── 选目录 ──────────────────────────────────────────────
    def pick_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="选择安装位置",
            initialdir=str(Path(self.target.get()).anchor or DEFAULT_DIR.anchor),
            mustexist=False,
        )
        if chosen:
            # 选中已有目录时，提示会把内容装进「该目录\ZhijingAIClient」而不是打散它
            p = Path(chosen)
            if p.name.lower() != "zhijingaiclient" and p != DEFAULT_DIR:
                p = p / "ZhijingAIClient"
            self.target.set(str(p))

    def progress(self, frac: float, text: str) -> None:
        self.bar["value"] = frac * 100
        self.status.config(text=text)
        self.update_idletasks()

    # ── 开始 ────────────────────────────────────────────────
    def start(self) -> None:
        target = Path(self.target.get().strip().strip('"'))
        if not str(target) or target.anchor == "":
            messagebox.showwarning("路径无效", "请填写一个完整的安装路径，例如 D:\\智鲸AI。")
            return

        err = check_target(target)
        if err:
            messagebox.showerror("无法使用这个位置", err)
            return

        if target.exists() and any(target.iterdir()):
            if not messagebox.askyesno(
                "目录已存在",
                f"{target}\n\n这个目录不是空的。\n"
                "如果是之前的智鲸安装，会被整体替换；\n"
                "如果是别的目录，只会写入同名文件、不会删除其它内容。\n\n继续吗？",
            ):
                return

        self.btn_install.config(state="disabled", text="安装中...")
        self.btn_cancel.config(state="disabled")
        self.browse.config(state="disabled")
        self.entry.config(state="disabled")

        def work() -> None:
            try:
                result = install(target, self.progress)
                self.after(0, lambda: self.done(result))
            except Exception as exc:  # noqa: BLE001
                log("FAILED: " + repr(exc))
                log(traceback.format_exc())
                self.after(0, lambda: self.failed(str(exc)))

        threading.Thread(target=work, daemon=True).start()

    # ── 结束状态 ────────────────────────────────────────────
    def done(self, result: dict) -> None:
        self.progress(1.0, "安装完成")
        self.hint.config(
            text=f"装到了 {result['target']}\n{result['note']}", fg="#2E7D32"
        )
        lines = []
        if result.get("password"):
            lines.append(f"管理员账号：admin / {result['password']}")
            lines.append("（这个密码只显示这一次，请记下）")
            lines.append("")
        lines.append("桌面上已生成「智鲸 AI 客户端」，双击启动。")
        lines.append("首次启动会自动安装运行环境（约 400–500 MB，需要联网）。")
        messagebox.showinfo("安装完成", "安装完成！\n\n" + "\n".join(lines))

        for w in self.buttons.winfo_children():
            w.destroy()
        tk.Button(
            self.buttons, text="启动客户端", width=12,
            font=("Microsoft YaHei UI", 10), bg="#1A4A7A", fg="white", relief="flat",
            command=lambda: self.launch(result["target"]),
        ).pack(side="left", padx=6)
        tk.Button(
            self.buttons, text="打开目录", width=10, font=("Microsoft YaHei UI", 10),
            command=lambda: os.startfile(str(result["target"])),  # noqa: S606
        ).pack(side="left", padx=6)
        tk.Button(
            self.buttons, text="关闭", width=8, font=("Microsoft YaHei UI", 10),
            command=self.destroy,
        ).pack(side="left", padx=6)

    def failed(self, msg: str) -> None:
        self.progress(1.0, "安装失败")
        self.status.config(fg="#C62828")
        self.hint.config(text=f"{msg}\n日志：{LOG}", fg="#C62828")
        messagebox.showerror(
            "安装失败",
            f"{msg}\n\n详细日志：\n{LOG}\n\n也可以改用源码包里的 install.cmd 手动安装。",
        )
        self.btn_install.config(state="normal", text="重试")
        self.btn_cancel.config(state="normal")
        self.browse.config(state="normal")
        self.entry.config(state="normal")

    def launch(self, target: Path) -> None:
        try:
            subprocess.Popen([str(Path(target) / "start.cmd")], cwd=str(target), shell=True)
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))


def run_silent(argv: list[str]) -> int:
    """无界面安装：`exe --silent --dir "D:\\智鲸AI"`。

    给批量部署用，也是本项目自测「自定义安装位置」这条链路的入口——
    GUI 点不出来，但走的必须是同一套 install()。
    """
    target = DEFAULT_DIR
    if "--dir" in argv:
        idx = argv.index("--dir")
        if idx + 1 < len(argv):
            target = Path(argv[idx + 1].strip('"'))
    err = check_target(target)
    if err:
        log("silent install refused: " + err)
        print(err, file=sys.stderr)
        return 2
    result = install(target, lambda frac, text: None)
    print(f"installed to {result['target']}")
    if result.get("password"):
        print(f"admin: admin / {result['password']}")
    print(result["note"])
    return 0


def main() -> int:
    log("=" * 56)
    log(f"installer start, bundle={bundle_dir()}")

    argv = sys.argv[1:]
    if "--silent" in argv:
        try:
            return run_silent(argv)
        except Exception as exc:  # noqa: BLE001
            log("SILENT FAILED: " + repr(exc))
            log(traceback.format_exc())
            print(f"failed: {exc}", file=sys.stderr)
            return 1

    app = Installer()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
