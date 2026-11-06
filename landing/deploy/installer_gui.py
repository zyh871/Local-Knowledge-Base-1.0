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

import hashlib
import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "智鲸 AI 客户端"

#: 配置档案（随包分发，也可以从远端下载覆盖）
PROFILE_SCHEMA = 1
PROFILE_REL = Path("config") / "profile.json"
SOURCE_REL = Path("config") / "source.json"
REPORT_REL = Path("config") / "applied.json"

#: 内置兜底档案：包里什么都没有时用它（全部走本机探测）
BUILTIN_PROFILE: dict = {
    "schema": PROFILE_SCHEMA,
    "name": "内置默认",
    "env": {},
    "search": {},
    "endpoints": [],
}
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


def detect_local_model_env() -> tuple[dict[str, str], str]:
    """探测本机 Ollama，返回 (要写进 .env 的键值, 说明)。"""
    local = probe_ollama()
    if local:
        base, model, embed = local
        env = {
            "MODEL_BACKEND": "ollama",
            "OLLAMA_BASE_URL": base,
            "LLM_MODEL": model,
        }
        if embed:
            env["EMBEDDING_MODEL"] = embed
            note = f"已检测到本地模型 {model}（向量模型 {embed}），启动后即可直接对话"
        else:
            # 没有向量模型时绝不能留空：默认的 nomic-embed-text 往往没装，
            # 而 Chroma 在嵌入不可用时会回落到内置 ONNX 模型，首次上传要联网下 79 MB。
            env["EMBEDDING_MODEL"] = "local-tfidf"
            note = f"已检测到本地模型 {model}，检索使用内置 TF-IDF（无需下载）"
        return env, note

    return (
        {
            "MODEL_BACKEND": "zhipu",
            "ZHIPU_API_KEY": "",
            "EMBEDDING_MODEL": "embedding-3",
        },
        "未检测到本地模型，需要用云端模型（启动前请填 ZHIPU_API_KEY）",
    )


# ---------------------------------------------------------------------------
#  配置档案：嵌入包内的默认值 + 可选远端下发
# ---------------------------------------------------------------------------
def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _valid_profile(data: object) -> tuple[bool, str]:
    """校验档案结构。宁可整份丢弃，也不要写进半份坏配置。"""
    if not isinstance(data, dict):
        return False, "不是 JSON 对象"
    schema = data.get("schema")
    if not isinstance(schema, int):
        return False, "缺少 schema 版本号"
    if schema > PROFILE_SCHEMA:
        return False, f"schema={schema} 高于本安装器支持的 {PROFILE_SCHEMA}"
    for key in ("env", "search"):
        value = data.get(key, {})
        if value and not isinstance(value, dict):
            return False, f"{key} 必须是对象"
    endpoints = data.get("endpoints", [])
    if endpoints and not isinstance(endpoints, list):
        return False, "endpoints 必须是数组"
    for item in endpoints:
        if not isinstance(item, dict) or not item.get("model") or not item.get("base_url"):
            return False, "endpoints 里有条目缺少 model / base_url"
    return True, ""


def load_packaged_profile(target: Path) -> tuple[dict | None, str]:
    """包内档案：解压后就在 config/profile.json。"""
    path = target / PROFILE_REL
    if not path.is_file():
        return None, "包内没有 profile.json"
    data = _read_json(path)
    if data is None:
        return None, "包内 profile.json 解析失败"
    ok, why = _valid_profile(data)
    if not ok:
        return None, f"包内 profile.json 不可用：{why}"
    return data, f"包内档案（{data.get('name') or '未命名'}）"


def fetch_remote_profile(url: str, expect_sha256: str = "", timeout: float = 12.0) -> tuple[dict | None, str]:
    """下载远端档案。失败一律返回 (None, 原因)，由调用方回退到下一级来源。

    远端配置的价值在于「同一个安装包，不同客户下发不同配置」，
    所以它必须是**可选**的：拉不到就退到包内，绝不能因此装不上。
    """
    if not url:
        return None, "未配置远端地址"
    last = ""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZhijingInstaller/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(512 * 1024)  # 配置不该超过 512 KB，超了当异常
            if expect_sha256:
                actual = hashlib.sha256(raw).hexdigest()
                if actual.lower() != expect_sha256.lower():
                    return None, f"sha256 不匹配（期望 {expect_sha256[:8]}…，实际 {actual[:8]}…）"
            data = json.loads(raw.decode("utf-8"))
            ok, why = _valid_profile(data)
            if not ok:
                return None, f"远端配置不可用：{why}"
            return data, f"远端配置（{data.get('name') or url}）"
        except Exception as exc:  # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
    return None, f"下载失败（{last}）"


def resolve_profile(target: Path, cli_url: str = "") -> tuple[dict, dict]:
    """按 远端 → 包内 → 内置 的顺序取档案，返回 (profile, 来源信息)。

    来源信息会写进 applied.json，出问题时能一眼看出「用的是哪份配置」。
    """
    attempts: list[dict] = []

    # 远端地址可以来自命令行，也可以来自包内的 source.json
    source = _read_json(target / SOURCE_REL) or {}
    url = cli_url or str(source.get("config_url") or "")
    sha = str(source.get("sha256") or "")

    if url:
        data, why = fetch_remote_profile(url, sha)
        attempts.append({"source": "remote", "url": url, "ok": bool(data), "detail": why})
        if data:
            return data, {"kind": "remote", "url": url, "detail": why, "attempts": attempts}

    data, why = load_packaged_profile(target)
    attempts.append({"source": "packaged", "ok": bool(data), "detail": why})
    if data:
        return data, {"kind": "packaged", "detail": why, "attempts": attempts}

    attempts.append({"source": "builtin", "ok": True, "detail": "使用内置默认档案"})
    return dict(BUILTIN_PROFILE), {"kind": "builtin", "detail": "使用内置默认档案", "attempts": attempts}


# ---------------------------------------------------------------------------
#  把档案写进安装目录
# ---------------------------------------------------------------------------
_ENV_LINE = re.compile(r"\s*([A-Z0-9_]+)\s*=")


def merge_env_file(path: Path, values: dict[str, str]) -> list[str]:
    """按键合并写 .env：已有的键就地替换，没有的追加；注释与顺序保留。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    index: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = _ENV_LINE.match(line)
        if m:
            index[m.group(1)] = i

    written: list[str] = []
    for key, value in values.items():
        line = f"{key}={value}"
        if key in index:
            if lines[index[key]] == line:
                continue
            lines[index[key]] = line
        else:
            lines.append(line)
        written.append(key)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def _seed_search_db(target: Path, search: dict) -> str | None:
    """把档案里的搜索配置写进 data/search.db（只在本地没配过时写）。"""
    provider = str(search.get("provider") or "")
    if not provider:
        return None
    db = target / "backend" / "data" / "search.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    try:
        import sqlite3

        conn = sqlite3.connect(db, timeout=10)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS search_config ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '', "
                "updated_at REAL NOT NULL DEFAULT 0)"
            )
            row = conn.execute(
                "SELECT value FROM search_config WHERE key = 'provider'"
            ).fetchone()
            if row and row[0]:
                return "本地已有搜索配置，保留不动"
            now = time.time()
            payload = [("provider", provider, now)]
            if search.get("api_key"):
                payload.append(("api_key", str(search["api_key"]), now))
            conn.executemany(
                "INSERT INTO search_config(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                payload,
            )
            conn.commit()
            return f"已写入搜索配置：{provider}"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return f"搜索配置写入失败：{exc}"


def _seed_model_endpoints(target: Path, endpoints: list[dict]) -> str | None:
    """把档案里的模型端点写进 data/custom_models.db（只在表为空时写）。"""
    if not endpoints:
        return None
    db = target / "backend" / "data" / "custom_models.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    try:
        import sqlite3

        conn = sqlite3.connect(db, timeout=10)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS model_endpoints ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL, "
                "api_key TEXT NOT NULL, model TEXT NOT NULL, "
                "enabled INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL DEFAULT '', "
                "last_test_at REAL, last_test_ok INTEGER, last_test_error TEXT, "
                "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
            )
            count = conn.execute("SELECT COUNT(*) FROM model_endpoints").fetchone()[0]
            if count:
                return "本地已有自定义模型，保留不动"
            now = time.time()
            rows = []
            for i, item in enumerate(endpoints):
                rows.append(
                    (
                        f"seed_{i}_{int(now)}",
                        str(item.get("name") or item.get("model")),
                        str(item["base_url"]),
                        str(item.get("api_key") or ""),
                        str(item["model"]),
                        1 if item.get("enabled", True) else 0,
                        "installer",
                        now,
                        now,
                    )
                )
            conn.executemany(
                "INSERT INTO model_endpoints (id, name, base_url, api_key, model, enabled, "
                "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.commit()
            return f"已预置 {len(rows)} 个模型端点"
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return f"模型端点写入失败：{exc}"


def apply_profile(target: Path, profile: dict, *, force: bool = False) -> dict:
    """把档案落到 .env 与两个 SQLite 库。返回结果摘要（写进 applied.json）。"""
    env_file = target / "backend" / ".env"
    existing_keys: set[str] = set()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = _ENV_LINE.match(line)
            if m:
                existing_keys.add(m.group(1))

    values: dict[str, str] = {}

    # 1) 档案里的键：默认只补缺，force 时整体覆盖
    for key, value in (profile.get("env") or {}).items():
        if force or key not in existing_keys:
            values[key] = str(value)

    # 2) 管理员与密钥：缺就生成，绝不能留空（后端会直接启动失败）
    password = None
    if force or "ADMIN_USERNAME" not in existing_keys:
        values.setdefault("ADMIN_USERNAME", "admin")
    if force or "ADMIN_PASSWORD" not in existing_keys:
        password = values.get("ADMIN_PASSWORD") or "".join(
            random.choice(string.ascii_letters + string.digits) for _ in range(12)
        )
        values["ADMIN_PASSWORD"] = password
    if force or "JWT_SECRET_KEY" not in existing_keys:
        values["JWT_SECRET_KEY"] = "".join(
            random.choice("0123456789abcdef") for _ in range(64)
        )

    # 3) 模型：档案没指定后端时才做本机探测，避免覆盖运维的意图
    note = ""
    if "MODEL_BACKEND" not in values and "MODEL_BACKEND" not in existing_keys:
        detected, note = detect_local_model_env()
        for key, value in detected.items():
            values.setdefault(key, value)
        # 云端方案的空密钥也要写出来，方便用户就地补
        if detected.get("MODEL_BACKEND") == "zhipu":
            values.setdefault("ZHIPU_API_KEY", "")
    elif values.get("MODEL_BACKEND") == "ollama" and "LLM_MODEL" not in values:
        detected, note = detect_local_model_env()
        for key in ("OLLAMA_BASE_URL", "LLM_MODEL", "EMBEDDING_MODEL"):
            if key in detected:
                values.setdefault(key, detected[key])

    written = merge_env_file(env_file, values)

    result = {
        "profile": profile.get("name") or "",
        "env_written": written,
        "password": password,
        "note": note,
        "search": _seed_search_db(target, profile.get("search") or {}),
        "endpoints": _seed_model_endpoints(target, profile.get("endpoints") or []),
    }
    return result


def write_report(target: Path, source: dict, applied: dict, deps: dict) -> Path:
    """把「这次安装用了哪份配置、写了什么、依赖装没装上」落盘。"""
    path = target / REPORT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "installer_version": APP_VERSION,
        "target": str(target),
        "profile": {
            "source": source.get("kind"),
            "detail": source.get("detail"),
            "attempts": source.get("attempts", []),
        },
        "applied": {
            "env_keys": applied.get("env_written", []),
            "search": applied.get("search"),
            "endpoints": applied.get("endpoints"),
            "note": applied.get("note"),
        },
        "dependencies": deps,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
#  依赖自动安装
# ---------------------------------------------------------------------------
def run_deps(target: Path, progress=None) -> dict:
    """自动跑 setup.ps1（建 venv + 装依赖）。

    失败不抛异常：文件已经装好了，依赖可以稍后补。
    返回 {"ok": bool, "returncode": int, "tail": "最后几行"}。
    """
    script = target / "setup.ps1"
    if not script.exists():
        return {"ok": False, "returncode": -1, "tail": "缺少 setup.ps1"}
    if (target / "backend" / ".venv" / "Scripts" / "python.exe").exists():
        return {"ok": True, "returncode": 0, "tail": "已存在虚拟环境，跳过"}

    exe = shutil.which("powershell") or "powershell"
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            cwd=str(target),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
    except OSError as exc:
        return {"ok": False, "returncode": -1, "tail": f"无法启动 PowerShell：{exc}"}

    tail: list[str] = []
    pulse = 0.0
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        log("setup: " + line)
        tail.append(line)
        tail[:] = tail[-12:]
        if progress:
            # 依赖安装没有可靠的总量，进度条只做「还在动」的提示
            pulse = min(pulse + 0.01, 0.12)
            progress(0.86 + pulse, "正在安装运行环境（约 400–500 MB，首次较慢）…")
    rc = proc.wait()
    return {"ok": rc == 0, "returncode": rc, "tail": "\n".join(tail[-6:])}


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


def install(
    target: Path,
    progress,
    *,
    install_deps: bool = True,
    config_url: str = "",
    force_config: bool = False,
) -> dict:
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

    # ── 配置：远端 → 包内 → 内置，逐级回退 ──────────────────
    progress(0.85, "写入配置...")
    profile, source = resolve_profile(target, config_url)
    log(f"config source: {source['kind']} ({source['detail']})")
    for attempt in source.get("attempts", []):
        log(f"  try {attempt['source']}: ok={attempt['ok']} {attempt['detail']}")

    applied = apply_profile(target, profile, force=force_config)
    log(
        "config applied: env="
        + ",".join(applied.get("env_written") or [])
        + f" | search={applied.get('search')} | endpoints={applied.get('endpoints')}"
    )

    # ── 运行环境：装得上就一步到位，装不上也不影响已装好的文件 ──
    if install_deps:
        progress(0.86, "正在安装运行环境（首次约 400–500 MB）…")
        deps = run_deps(target, progress)
    else:
        deps = {"ok": False, "skipped": True, "tail": "安装时未选择安装运行环境"}
    log(f"deps: ok={deps.get('ok')} skipped={deps.get('skipped')} rc={deps.get('returncode')}")

    report_path = write_report(target, source, applied, deps)
    log(f"report: {report_path}")

    progress(1.0, "完成")
    log(f"install ok: target={target} launchers={made}")
    return {
        "target": target,
        "note": applied.get("note") or "",
        "password": applied.get("password"),
        "launchers": made,
        "config_source": source,
        "deps": deps,
        "report": str(report_path),
    }


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

        # 依赖安装默认勾选：一步到位；不想等也可以取消，之后双击 setup.cmd 补
        self.deps_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            self,
            text="同时安装运行环境（首次约 400–500 MB，需联网，约 5 分钟）",
            variable=self.deps_var,
            font=("Microsoft YaHei UI", 9),
            bg="#F7F9FC", fg="#1A2330", activebackground="#F7F9FC",
            selectcolor="white", anchor="w",
        ).pack(anchor="w", padx=24, pady=(10, 0))

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

        want_deps = bool(self.deps_var.get())

        def work() -> None:
            try:
                result = install(target, self.progress, install_deps=want_deps)
                self.after(0, lambda: self.done(result))
            except Exception as exc:  # noqa: BLE001
                log("FAILED: " + repr(exc))
                log(traceback.format_exc())
                self.after(0, lambda: self.failed(str(exc)))

        threading.Thread(target=work, daemon=True).start()

    # ── 结束状态 ────────────────────────────────────────────
    def done(self, result: dict) -> None:
        self.progress(1.0, "安装完成")
        source = result.get("config_source") or {}
        deps = result.get("deps") or {}
        summary = f"装到了 {result['target']}\n配置来源：{source.get('detail') or '未知'}"
        if result.get("note"):
            summary += f"\n{result['note']}"
        self.hint.config(text=summary, fg="#2E7D32")

        lines = [f"配置来源：{source.get('detail') or '未知'}"]
        if result.get("password"):
            lines.append(f"管理员账号：admin / {result['password']}")
            lines.append("（这个密码只显示这一次，请记下）")
        lines.append("")

        if deps.get("ok"):
            lines.append("运行环境已装好，现在就能用。")
        elif deps.get("skipped"):
            lines.append("已跳过运行环境安装：首次启动前请双击 setup.cmd，或点下面的「安装运行环境」。")
        else:
            lines.append("⚠ 运行环境没装成功（多半是网络或 pip 源的问题）。")
            lines.append("   文件已经装好，点下面的「重试安装环境」即可，不必重装。")

        lines.append("")
        lines.append("桌面上已生成「智鲸 AI 客户端」。")
        lines.append(f"安装报告：{result.get('report')}")
        messagebox.showinfo("安装完成", "安装完成！\n\n" + "\n".join(lines))

        for w in self.buttons.winfo_children():
            w.destroy()
        if not deps.get("ok"):
            tk.Button(
                self.buttons, text="重试安装环境", width=14,
                font=("Microsoft YaHei UI", 10),
                command=lambda: self.retry_deps(result["target"]),
            ).pack(side="left", padx=6)
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

    def retry_deps(self, target: Path) -> None:
        """单独补装运行环境。装不上不阻塞其它操作，随时可再试。"""
        for w in self.buttons.winfo_children():
            w.config(state="disabled")

        def work() -> None:
            deps = run_deps(Path(target), self.progress)
            self.after(0, lambda: self._deps_done(deps))

        threading.Thread(target=work, daemon=True).start()

    def _deps_done(self, deps: dict) -> None:
        for w in self.buttons.winfo_children():
            w.config(state="normal")
        if deps.get("ok"):
            self.progress(1.0, "运行环境已就绪")
            self.hint.config(text="运行环境已装好，可以启动了。", fg="#2E7D32")
            messagebox.showinfo("完成", "运行环境已装好，现在可以启动客户端。")
        else:
            self.progress(1.0, "运行环境安装失败")
            messagebox.showerror(
                "仍然失败",
                "运行环境没装上。\n\n"
                f"{deps.get('tail', '')}\n\n"
                "常见原因：网络不可达 / 需要代理 / PyPI 源不通。\n"
                "可以换国内源重试：在安装目录里打开命令行执行\n"
                "  backend\\.venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt "
                "-i https://pypi.tuna.tsinghua.edu.cn/simple",
            )

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

    config_url = ""
    if "--config-url" in argv:
        idx = argv.index("--config-url")
        if idx + 1 < len(argv):
            config_url = argv[idx + 1].strip('"')

    err = check_target(target)
    if err:
        log("silent install refused: " + err)
        print(err, file=sys.stderr)
        return 2
    result = install(
        target,
        lambda frac, text: None,
        install_deps="--no-deps" not in argv,
        config_url=config_url,
        force_config="--force-config" in argv,
    )
    print(f"installed to {result['target']}")
    source = result.get("config_source") or {}
    print(f"config: {source.get('kind')} — {source.get('detail')}")
    if result.get("password"):
        print(f"admin: admin / {result['password']}")
    if result["note"]:
        print(result["note"])
    deps = result.get("deps") or {}
    if deps.get("skipped"):
        print("deps: skipped（稍后双击 setup.cmd 安装）")
    elif deps.get("ok"):
        print("deps: ok")
    else:
        print(f"deps: FAILED rc={deps.get('returncode')} — 可稍后双击 setup.cmd 重试")
        print(deps.get("tail", ""))
    print(f"report: {result.get('report')}")
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
