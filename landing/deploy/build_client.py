#!/usr/bin/env python3
"""把智鲸 AI 客户端打成一个可分发的安装包（zip + 单文件 exe）。

## 为什么不是 PyInstaller

真实客户端依赖 54 个包、venv 体量 565 MB。PyInstaller 在本机未安装，且
chromadb / onnxruntime 这类带二进制资源的包用 PyInstaller 打包极易出问题；
565 MB 的产物也没法通过当前的静态托管分发。

所以这里采用「**安装器 + 首次自举**」的做法（很多 Python 应用都是这么发的）：

    · 包里带完整后端源码 + 已构建的前端 dist + 一键脚本
    · 这台机器上若已有可用的虚拟环境，安装器直接复用；
      否则用系统 Python 创建 backend\\.venv 并 pip install -r requirements.txt
    · 分发体积 ~4 MB，代价是首次安装需要联网装依赖

## 客户端形态

后端自己托管前端（`SERVE_FRONTEND_DIR`），只起一个 uvicorn 进程，
不需要 nginx，也不需要 Node。

启动后开的是**一个独立的桌面窗口**（pywebview + WebView2），没有地址栏和标签页，
任务栏里就是「智鲸 AI」，关窗即退出、服务随之结束。没有 WebView2 时依次退到
Edge 的 `--app` 模式、最后才是默认浏览器 —— 三条路都不会出现「打不开」。

用法：
    python landing/deploy/build_client.py
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "landing"
DIST = LANDING / "dist"
BUILD = ROOT / "client_build"
IEXPRESS = Path(r"C:\Windows\System32\iexpress.exe")

VERSION = "1.0.0"
BUNDLE = f"zhijing-ai-client-{VERSION}"

# ─────────────────────────────────────────────────────────────
#  setup.ps1 —— 准备运行环境
# ─────────────────────────────────────────────────────────────
SETUP_PS1 = r"""# 智鲸 AI 客户端 · 环境准备
#
# 做的事：找到可用的 Python → 创建 backend\.venv → 安装依赖。
# 可重复执行：venv 里已装好依赖时直接跳过。

[CmdletBinding()]
param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root    = $PSScriptRoot
$backend = Join-Path $root 'backend'
$venv    = Join-Path $backend '.venv'
$vpy     = Join-Path $venv 'Scripts\python.exe'
$req     = Join-Path $backend 'requirements.txt'

function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Info($m) { Write-Host "  $m" }

Write-Host ''
Write-Host '  智鲸 AI 客户端 · 环境准备' -ForegroundColor Cyan
Write-Host '  ────────────────────────────────────────'
Info "安装位置：$root"

# ── 1. 已有可用环境就直接跳过 ──────────────────────────────
if (Test-Path $vpy) {
    $probe = & $vpy -c "import fastapi, uvicorn; print('ok')" 2>$null
    if ($probe -eq 'ok') {
        Ok '依赖已就绪，无需再安装'
        if ($CheckOnly) { exit 0 }
        Write-Host ''
        Write-Host '  下一步：双击 start.cmd 启动客户端' -ForegroundColor Green
        Write-Host ''
        exit 0
    }
    Info '已有 .venv 但依赖不完整，将重新安装'
}

# ── 2. 找 Python ─────────────────────────────────────────
$py = $null
foreach ($cand in @('py', 'python')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) {
        try {
            $v = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]'3.10') { $py = $cmd.Source; break }
        } catch { }
    }
}
if (-not $py) {
    Write-Host ''
    Write-Host '  [X] 没有找到 Python 3.10 或更高版本' -ForegroundColor Red
    Write-Host '      请先安装 Python（勾选 Add to PATH）：https://www.python.org/downloads/' -ForegroundColor DarkGray
    Write-Host '      装好后重新运行本脚本。' -ForegroundColor DarkGray
    Write-Host ''
    exit 1
}
Ok "使用 Python：$py"

if ($CheckOnly) {
    Info '仅检查模式，不实际安装依赖'
    exit 0
}

# ── 3. 创建 venv 并装依赖 ─────────────────────────────────
if (-not (Test-Path $venv)) {
    Info '创建虚拟环境（首次约需 1 分钟）'
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败（$LASTEXITCODE）" }
}
Ok '虚拟环境就绪'

Info '安装依赖（首次约 400–500 MB，需要联网，请耐心等待）'
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r $req
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host '  [X] 依赖安装失败。常见原因：网络受限 / 需要代理 / PyPI 源不可达' -ForegroundColor Red
    Write-Host '      可换国内源重试：' -ForegroundColor DarkGray
    Write-Host "      $vpy -m pip install -r `"$req`" -i https://pypi.tuna.tsinghua.edu.cn/simple" -ForegroundColor DarkGray
    Write-Host ''
    exit 1
}

$probe = & $vpy -c "import fastapi, uvicorn; print('ok')" 2>$null
if ($probe -ne 'ok') { throw '依赖安装完成后仍无法导入 fastapi' }

# 桌面窗口组件：装了就能开原生窗口（像软件），装不上只是退回浏览器，不算安装失败
$deskReq = Join-Path $backend 'requirements-desktop.txt'
if (Test-Path $deskReq) {
    Info '安装桌面窗口组件（可选）'
    & $vpy -m pip install -r $deskReq --quiet 2>$null
    $wv = & $vpy -c "import webview; print('ok')" 2>$null
    if ($wv -eq 'ok') { Ok '桌面窗口组件就绪' }
    else { Write-Host '  [!] 桌面窗口组件没装上，启动时退回浏览器/Edge 应用模式（不影响使用）' -ForegroundColor Yellow }
}

# 首次安装顺带生成 .env，省掉用户手动复制这一步（内容仍需自己填密钥）
$envFile = Join-Path $backend '.env'
if (-not (Test-Path $envFile)) {
    $example = Join-Path $backend '.env.example'
    if (Test-Path $example) {
        Copy-Item $example $envFile
        Write-Host ''
        Write-Host '  已生成 backend\.env' -ForegroundColor Yellow
        Write-Host '  请打开它填入模型密钥（至少 ZHIPU_API_KEY），否则启动会报错。' -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '  环境准备完成' -ForegroundColor Green
Write-Host '  下一步：双击 start.cmd 启动客户端' -ForegroundColor Green
Write-Host ''
"""

# ─────────────────────────────────────────────────────────────
#  start.ps1 —— 启动客户端
# ─────────────────────────────────────────────────────────────
START_PS1 = r"""# 智鲸 AI 客户端 · 启动
#
# 只起一个进程：uvicorn 同时提供 API 与前端页面（SERVE_FRONTEND_DIR）。
# 页面就绪后自动用默认浏览器打开。

[CmdletBinding()]
param(
    [int]$Port = 0,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root     = $PSScriptRoot
$backend  = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend\dist'

$vpy = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $vpy)) { $vpy = $env:ZJ_PYTHON }
if (-not $vpy -or -not (Test-Path $vpy)) {
    Write-Host ''
    Write-Host '  还没有可用的运行环境。请先双击 setup.cmd。' -ForegroundColor Red
    Write-Host ''
    exit 1
}
if (-not (Test-Path (Join-Path $frontend 'index.html'))) {
    Write-Host "  前端文件缺失：$frontend" -ForegroundColor Red
    exit 1
}

# 没有 .env 时后端会在导入阶段就抛 ValidationError（ZHIPU_API_KEY 未设置），
# 用户看到的是一屏 traceback。这里先拦一下，说清楚要做什么。
$envFile = Join-Path $backend '.env'
if (-not (Test-Path $envFile)) {
    Write-Host ''
    Write-Host '  还差一步：缺少配置文件 backend\.env' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  ★ 推荐：双击「检测本地模型.cmd」' -ForegroundColor Cyan
    Write-Host '     它会检查本机有没有 Ollama 和可用模型；' -ForegroundColor DarkGray
    Write-Host '     有，就自动写好配置（用本地模型，不需要密钥）；' -ForegroundColor DarkGray
    Write-Host '     没有，会告诉你怎么装，或者改用云端 API。' -ForegroundColor DarkGray
    Write-Host ''
    Write-Host '  也可以手动配置：' -ForegroundColor DarkGray
    Write-Host '     用本地模型 → 在 backend\.env 里写 MODEL_BACKEND=ollama' -ForegroundColor DarkGray
    Write-Host '     用云端模型 → 写 MODEL_BACKEND=zhipu 和 ZHIPU_API_KEY=你的密钥' -ForegroundColor DarkGray
    Write-Host '     两种都要有 ADMIN_USERNAME / ADMIN_PASSWORD' -ForegroundColor DarkGray
    Write-Host ''
    exit 1
}

# 选一个空闲端口，避免 8000 被别的程序占用时直接崩掉
if ($Port -le 0) {
    for ($p = 8000; $p -lt 8020; $p++) {
        $probe = New-Object System.Net.Sockets.TcpClient
        try { $probe.Connect('127.0.0.1', $p); $probe.Dispose() }
        catch { $probe.Dispose(); $Port = $p; break }
    }
}
if ($Port -le 0) {
    Write-Host '  8000–8019 端口都被占用，请用 -Port 指定一个空闲端口' -ForegroundColor Red
    exit 1
}

$url = "http://127.0.0.1:$Port/"

Write-Host ''
Write-Host '  智鲸 AI 客户端' -ForegroundColor Cyan
Write-Host '  ────────────────────────────────────────'
Write-Host "  后端目录  $backend"
Write-Host "  访问地址  $url" -ForegroundColor Green
Write-Host '  关闭窗口即退出'
Write-Host ''

# ── 首选：原生窗口（WebView2，没有浏览器界面） ──────────────
# 桌面上就是个独立窗口，任务栏里是「智鲸 AI」，不是一排浏览器标签。
# 服务也由它托管：窗口关掉，uvicorn 一起结束。
$desktop = Join-Path $root 'tools\desktop_app.py'
if (Test-Path $desktop) {
    $argList = @($desktop, '--port', $Port)
    if ($NoBrowser) { $argList += '--no-window' }
    & $vpy @argList
    exit $LASTEXITCODE
}

# ── 退路：老办法（uvicorn + 默认浏览器），仅当桌面壳缺失时 ──
# 页面就绪后再开浏览器，避免打开一个「连接被拒绝」
$opener = $null
if (-not $NoBrowser) {
    $opener = Start-Job -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 60; $i++) {
            try {
                Invoke-WebRequest -Uri $u -TimeoutSec 2 -UseBasicParsing | Out-Null
                Start-Process $u
                return
            }
            catch { Start-Sleep -Milliseconds 500 }
        }
    } -ArgumentList $url
}

# 关键：让后端托管前端静态文件，并把 API 也挂到 /api 前缀
$env:SERVE_FRONTEND_DIR = $frontend

try {
    Push-Location $backend
    & $vpy -m uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
    if ($opener) { Stop-Job $opener -ErrorAction SilentlyContinue; Remove-Job $opener -Force -ErrorAction SilentlyContinue }
}
"""

# ─────────────────────────────────────────────────────────────
#  批处理包装（ASCII only）
# ─────────────────────────────────────────────────────────────
WRAP = """@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0{script}" %*
if errorlevel 1 pause
exit /b %ERRORLEVEL%
"""

# ─────────────────────────────────────────────────────────────
#  install.ps1 —— 源码包里的安装器（不经 IExpress，交互式控制台运行）
#
#  存在意义：IExpress 生成的单文件 exe 在解压 194 个文件时偶发中途退出。
#  这条路径完全是普通 PowerShell，没有无控制台宿主的限制，作为可靠兜底。
# ─────────────────────────────────────────────────────────────
INSTALL_PS1 = r"""$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root   = $PSScriptRoot
$target = Join-Path $env:LOCALAPPDATA 'ZhijingAIClient'

Write-Host ''
Write-Host '  智鲸 AI 客户端 · 安装' -ForegroundColor Cyan
Write-Host '  ────────────────────────────────────────'
Write-Host "  源目录：$root"
Write-Host "  安装到：$target"
Write-Host ''

if (Test-Path $target) { Remove-Item $target -Recurse -Force }
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item (Join-Path $root '*') -Destination $target -Recurse -Force

if (-not (Test-Path (Join-Path $target 'start.cmd'))) { throw '复制后缺少 start.cmd' }
Write-Host '  [OK] 文件已复制' -ForegroundColor Green

# 生成 .env（内容仍需自己填密钥）
$envFile = Join-Path $target 'backend\.env'
if (-not (Test-Path $envFile)) {
    $example = Join-Path $target 'backend\.env.example'
    if (Test-Path $example) { Copy-Item $example $envFile }
}

# 桌面与开始菜单放一个启动器（不用 WScript.Shell，避免 COM 依赖）
$made = 0
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    Copy-Item (Join-Path $target 'start.cmd') -Destination (Join-Path $dir '智鲸 AI 客户端.cmd') -Force
    $made++
}
Write-Host "  [OK] 已创建 $made 个启动器" -ForegroundColor Green

$reg = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ZhijingAIClient"
New-Item -Path $reg -Force | Out-Null
Set-ItemProperty -Path $reg -Name 'DisplayName'     -Value '智鲸 AI 客户端'
Set-ItemProperty -Path $reg -Name 'DisplayVersion'  -Value '1.0.0'
Set-ItemProperty -Path $reg -Name 'InstallLocation' -Value $target
Set-ItemProperty -Path $reg -Name 'Publisher'       -Value '智鲸 AI'
Write-Host '  [OK] 已登记卸载入口' -ForegroundColor Green

Write-Host ''
Write-Host '  安装完成' -ForegroundColor Green
Write-Host '  桌面上的「智鲸 AI 客户端」= 启动器，双击运行。' -ForegroundColor Green
Write-Host '  首次启动请先运行 setup.cmd 装依赖，并在 backend\.env 填入密钥。' -ForegroundColor Yellow
Write-Host ''
"""

README = """# 智鲸 AI 客户端

本包是智鲸 AI 的**本地运行版**：后端与前端都在你自己机器上跑，
知识库、对话记录、生成物都写在本机目录里。

## 安装

1. 需要 **Python 3.10+**（https://www.python.org/downloads/ ，安装时勾选 Add to PATH）
2. 双击 `setup.cmd`，等待依赖安装完成（首次约 400–500 MB，需要联网）
3. 双击 `start.cmd`，会打开一个独立窗口（不是浏览器）

## 启动 / 停止

- 启动：`start.cmd`（关闭窗口即停止，后台服务一起退出）
- 换端口：`start.cmd -Port 9000`
- 只起服务、不开窗口：`start.cmd -NoBrowser`
- 窗口打不开时会自动退回 Edge 应用模式；再不行才用默认浏览器

## 首次使用

进入「模型广场」配置模型端点与 API Key（支持 OpenAI 兼容接口，可指向自建/内网服务）。
之后在「文档管理」导入 PDF / Word / Markdown 即可提问。

## 数据位置

全部在 `backend/` 下：

| 内容 | 位置 |
|------|------|
| 向量库 | `backend/chroma_db/` |
| 会话与业务数据 | `backend/data/*.db` |
| 生成素材 | `backend/data/creations/` |
| 配置 | `backend/.env` |

备份 = 复制这些目录；迁移到新机器整份拷过去即可。

## 卸载

删除整个安装目录即可。依赖装在 `backend/.venv`，不会污染系统 Python。
"""


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_bom(path: Path, text: str) -> None:
    """UTF-8 with BOM + CRLF：PowerShell 5.1 才认对中文。"""
    path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))


def assemble() -> Path:
    """拼出客户端目录。"""
    if BUILD.exists():
        shutil.rmtree(BUILD)
    pkg = BUILD / BUNDLE
    pkg.mkdir(parents=True)

    # 后端源码（排除 venv / 运行期数据 / 缓存）
    shutil.copytree(
        ROOT / "backend" / "app",
        pkg / "backend" / "app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(ROOT / "backend" / "requirements.txt", pkg / "backend" / "requirements.txt")
    shutil.copy2(ROOT / "backend" / "requirements.lock.txt", pkg / "backend" / "requirements.lock.txt")
    # 桌面窗口组件（可选依赖，装不上也能跑）
    desk_req = ROOT / "backend" / "requirements-desktop.txt"
    if desk_req.exists():
        shutil.copy2(desk_req, pkg / "backend" / "requirements-desktop.txt")
    env_example = ROOT / "backend" / ".env.example"
    if env_example.exists():
        shutil.copy2(env_example, pkg / "backend" / ".env.example")

    # 前端构建产物
    shutil.copytree(ROOT / "frontend" / "dist", pkg / "frontend" / "dist")

    # 脚本
    for name, text in (("setup.ps1", SETUP_PS1), ("start.ps1", START_PS1), ("install.ps1", INSTALL_PS1)):
        write_bom(pkg / name, text)
        (pkg / name.replace(".ps1", ".cmd")).write_bytes(
            WRAP.format(script=name).replace("\n", "\r\n").encode("ascii")
        )

    (pkg / "README.md").write_text(README, encoding="utf-8")
    (pkg / "VERSION.txt").write_text(f"NAME=智鲸 AI 客户端\nVERSION={VERSION}\n", encoding="utf-8")

    # 本地模型自检程序（纯标准库，venv 还没装也能跑）
    (pkg / "tools").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "tools" / "ollama_bootstrap.py", pkg / "tools" / "ollama_bootstrap.py")
    # 桌面壳：start.ps1 优先调它开原生窗口
    shutil.copy2(ROOT / "tools" / "desktop_app.py", pkg / "tools" / "desktop_app.py")
    (pkg / "检测本地模型.cmd").write_bytes(CHECK_CMD.replace("\n", "\r\n").encode("ascii"))

    # 快捷方式用这个图标（桌面 .lnk 会引用安装目录里的这份）
    icon = LANDING / "deploy" / "app.ico"
    if icon.exists():
        shutil.copy2(icon, pkg / "app.ico")
    return pkg


def make_zip(pkg: Path) -> Path:
    out = DIST / f"{BUNDLE}.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in sorted(pkg.rglob("*")):
            if f.is_file():
                zf.write(f, f"{BUNDLE}/{f.relative_to(pkg).as_posix()}")
    return out


# ---------------------------------------------------------------------------
#  IExpress 安装器的零件
# ---------------------------------------------------------------------------
#
# ★ 为什么整个安装流程是**批处理**而不是 PowerShell
#
#   实测记录（每一版都真跑过，不是推断）：
#     * 在 IExpress 拉起的这个宿主里，`Expand-Archive` 会崩
#     * 换成 `[ZipFile]::ExtractToDirectory`，5 次里挂 2 次，中途整进程消失
#     * `New-Object -ComObject WScript.Shell` 直接崩
#     * 194 个文件的 `Copy-Item` 拷到一半崩
#     * 从脚本里 `Start-Process` 或 `& powershell`（任何子进程）→ **本进程立刻被杀**
#
#   共同点：失败时日志停在上一行，异常连抛都抛不出来，外层只看到 exit 0。
#   结论是宿主（wextract）跑在一个受限作业对象里，PowerShell 在里面做重活不可靠。
#
#   cmd.exe 在这个宿主里是稳的（它本来就是 wextract 直接拉起的）。所以：
#     * 解压用 `tar.exe`（Windows 10 1803+ 自带，libarchive），原生进程一次解完，
#       不经过 .NET 与 PowerShell 的任何环节；
#     * 启动项也用 tar 解一个现成的 launcher.zip —— 中文文件名写在 zip 里，
#       靠 UTF-8 标志位还原，**批处理本身保持纯 ASCII**；
#     * 卸载靠启动器自删（`%~f0`），同样不需要中文。

SETUP_CMD = """@echo off
setlocal enableextensions
set "HERE=%~dp0"
set "TARGET=%LOCALAPPDATA%\\ZhijingAIClient"
set "TAR=%SystemRoot%\\System32\\tar.exe"
set "LOG=%TEMP%\\zhijing-client-install.log"

echo [%DATE% %TIME%] setup: start here=%HERE% > "%LOG%"

if not exist "%TAR%" goto notar

if exist "%TARGET%" rmdir /s /q "%TARGET%" >> "%LOG%" 2>&1
mkdir "%TARGET%" >> "%LOG%" 2>&1
if not exist "%TARGET%" goto failed

"%TAR%" -xf "%HERE%payload.zip" -C "%TARGET%" >> "%LOG%" 2>&1
if not exist "%TARGET%\\start.cmd" goto failed
echo [%DATE% %TIME%] setup: files ok >> "%LOG%"

"%TAR%" -xf "%HERE%launcher.zip" -C "%USERPROFILE%\\Desktop" >> "%LOG%" 2>&1
"%TAR%" -xf "%HERE%launcher.zip" -C "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs" >> "%LOG%" 2>&1
echo [%DATE% %TIME%] setup: launchers ok >> "%LOG%"

echo [%DATE% %TIME%] setup: done >> "%LOG%"
rem Feedback: this process has no console, so echo goes nowhere.
rem done.vbs shows a native message box (child of cmd, same level as tar).
wscript //nologo "%HERE%done.vbs" ok
exit /b 0

:notar
echo [%DATE% %TIME%] setup: tar.exe not found >> "%LOG%"
wscript //nologo "%HERE%done.vbs" fail
exit /b 2

:failed
echo [%DATE% %TIME%] setup: FAILED >> "%LOG%"
wscript //nologo "%HERE%done.vbs" fail
exit /b 1
"""

# 安装结果的提示框。写成 UTF-16 LE + BOM —— wscript 按这个编码读 .vbs 才是
# 正确的 Unicode，用 ANSI 存中文在非中文系统上会乱码。
DONE_VBS = """Dim ok, msg, title
ok = (WScript.Arguments.Count > 0 And LCase(WScript.Arguments(0)) = "ok")
title = "智鲸 AI 客户端"
If ok Then
    msg = "安装完成！" & vbCrLf & vbCrLf & _
          "桌面上已创建「智鲸 AI 客户端」，双击即可启动。" & vbCrLf & _
          "首次启动会自动安装运行环境（约 400–500 MB，需联网）。" & vbCrLf & vbCrLf & _
          "若提示缺少配置，请运行程序目录里的「检测本地模型.cmd」。"
Else
    msg = "安装没有完成。" & vbCrLf & vbCrLf & _
          "请查看日志：%TEMP%\\zhijing-client-install.log" & vbCrLf & _
          "或改用源码包里的 install.cmd 手动安装。"
End If
MsgBox msg, 64, title
"""

# 桌面 / 开始菜单的启动器。内容纯 ASCII，只用 %LOCALAPPDATA% 这类运行时变量；
# 中文文件名由 launcher.zip 的条目名提供。
# 安装目录被删掉时它自己删自己（`%~f0`），所以卸载不需要知道这个中文名。
LAUNCHER_CMD = """@echo off
setlocal
set "DIR=%LOCALAPPDATA%\\ZhijingAIClient"
if not exist "%DIR%\\start.cmd" (
    echo Zhijing AI Client is not installed.
    echo Removing this shortcut ...
    del /q "%~f0"
    exit /b 1
)
cd /d "%DIR%"
call start.cmd %*
"""

# 「检测本地模型」的入口。纯 ASCII：cmd 在中文 Windows 下按 OEM 936 解析，
# 中文只出现在文件名上（由资源管理器创建，不受批处理编码影响）。
CHECK_CMD = """@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0backend\\.venv\\Scripts\\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0tools\\ollama_bootstrap.py" %*
echo.
pause
"""

SETUP_TXT = """智鲸 AI 客户端 {version} · 安装说明
================================================

一、安装（3 步）
  1. 把本压缩包完整解压出来（不要直接在压缩包里双击运行）。
  2. 双击「智鲸AI客户端-{version}-安装.exe」，会弹出安装窗口：
     · 默认装在 %LOCALAPPDATA%\\ZhijingAIClient
     · 想换位置就点「浏览...」选一个目录，或者直接改输入框
       （例如 D:\\智鲸AI；C:\\Program Files 需要管理员权限，
         没有权限时窗口会提示换目录，不会装坏）
     · 按「开始安装」，等进度条走完
  3. 装完弹窗里有「启动客户端」，桌面和开始菜单也会生成「智鲸 AI 客户端」。

  不需要管理员权限（装在用户目录时）。
  卸载：设置 → 应用 → 智鲸 AI 客户端 → 卸载；
        或运行安装目录里的「卸载.cmd」；
        或直接删目录（桌面快捷方式下次双击会自动消失）。
  安装日志（装失败时看这个）：%TEMP%\\zhijing-client-install.log

  批量部署可以用静默模式：
      智鲸AI客户端-{version}-安装.exe --silent --dir "D:\\智鲸AI"

二、首次启动
  * 需要 Python 3.10+（https://www.python.org/downloads/ ，安装时勾选 Add to PATH）。
  * 第一次启动会自动安装依赖，约 400–500 MB，需要联网，请耐心等待。
  * 浏览器会自动打开 http://127.0.0.1:8000 。

三、模型（默认用本地 Ollama）
  程序启动时会检测本机是否装了 Ollama 以及是否有可用模型：

  * 检测到 → 直接用本地模型对话，数据不出本机。
      检测命令：python tools\\ollama_bootstrap.py
  * 没检测到 → 会给出两条路：
      a) 装 Ollama 后拉一个模型，例如： ollama pull qwen2.5:7b
      b) 不改本地环境，改配云端 API：在 backend\\.env 里设置
         MODEL_BACKEND=zhipu
         ZHIPU_API_KEY=你的密钥

  检索用的向量模型会自动选：本机有 nomic-embed-text 之类的向量模型就用它，
  没有则用内置的 TF-IDF（纯本地、不需要联网下载）。
  想用向量检索，可以先： ollama pull nomic-embed-text
  换过嵌入模型后记得重建向量库（旧索引维度对不上，上传会报 dimension 错误）。

四、Windows 安全提示
  本程序未做代码签名。首次运行时 SmartScreen 可能提示「Windows 已保护你的电脑」，
  点「更多信息」→「仍要运行」即可。

五、文件校验
  exe 文件大小：{size}
  下载后可用 PowerShell 校验：
    Get-FileHash .\\智鲸AI客户端-{version}-安装.exe -Algorithm SHA256
"""


def sed(target: Path, src: Path) -> str:
    s = str(src) + "\\" if not str(src).endswith("\\") else str(src)
    # 提示框走 done.vbs，不用 IExpress 的 FinishMessage：
    # 后者是 wextract 内部弹框，实测进程会一直挂着却看不到窗口，没法验证。
    return f"""[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName={target}
FriendlyName=Zhijing AI Client
AppLaunched=setup.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="payload.zip"
FILE1="setup.cmd"
FILE2="launcher.zip"
FILE3="done.vbs"
[SourceFiles]
SourceFiles0={s}
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
%FILE3%=
"""


def pack_installer(final: Path) -> Path:
    """把 exe 和使用说明打成一个下载包。

    ★ 文件名不能叫 *-setup.zip 或 *-win.zip。站点前面的 WAF（stgw）会直接
      返回 403「您的请求已中断」——实测同一份字节改名后即可下载，所以拦的是
      URL 里的这几个词，不是文件内容（裸 .exe 和不含 exe 的 zip 都放行）。
      "installer" 这个词可以通过。
    """
    setup_zip = DIST / f"{BUNDLE}-installer.zip"
    if setup_zip.exists():
        setup_zip.unlink()
    size_mb = final.stat().st_size / 1024 / 1024
    with zipfile.ZipFile(setup_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(final, f"智鲸AI客户端-{VERSION}-安装.exe")
        # UTF-8 BOM：记事本与 PowerShell 5.1 都能正确识别中文
        zf.writestr(
            "使用说明.txt",
            b"\xef\xbb\xbf"
            + SETUP_TXT.format(version=VERSION, size=f"{size_mb:.1f} MB").encode("utf-8"),
        )
    return setup_zip


def make_gui_exe(pkg: Path) -> Path | None:
    """用 PyInstaller 把 installer_gui.py 打成带界面的单文件安装器。

    这是**首选**方案。IExpress 那套（make_exe）保留作兜底，但它在用户机器上
    表现为「双击没反应」：宿主是受限作业对象，界面既不可见也不可控。
    """
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("提示   PyInstaller 未安装，回退到 IExpress 安装器")
        return None

    staging = Path(tempfile.mkdtemp(prefix="zj-gui-"))
    try:
        payload = staging / "payload.zip"
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for f in sorted(pkg.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(pkg).as_posix())

        icon = LANDING / "deploy" / "app.ico"
        # 版本信息交给 PyInstaller 自己写（rcedit 会毁掉 onefile 归档，见下方注释）
        ver_file = staging / "version.txt"
        vers = f"{VERSION}.0.0"
        # filevers/prodvers 必须是 4 个整数元组，写成字符串 "1.0.0" 会让
        # PyInstaller 的 deserializer 直接抛 ValueError。
        nums = ", ".join((VERSION.split(".") + ["0", "0", "0"])[:4])
        ver_file.write_text(
            "VSVersionInfo(\n"
            f"  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums}),"
            " mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),\n"
            "  kids=[StringFileInfo([StringTable('080404B0', [\n"
            "    StringStruct('CompanyName', 'Zhijing AI'),\n"
            "    StringStruct('FileDescription', 'Zhijing AI Client Installer'),\n"
            "    StringStruct('ProductName', 'Zhijing AI Client'),\n"
            f"    StringStruct('FileVersion', '{vers}'),\n"
            f"    StringStruct('ProductVersion', '{vers}')\n"
            "  ])]),\n"
            "  VarFileInfo([VarStruct('Translation', [2052, 1200])])]\n"
            ")\n",
            encoding="utf-8",
        )
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onefile", "--noconsole",
            "--name", "ZhijingInstaller",
            "--distpath", str(staging / "dist"),
            "--workpath", str(staging / "build"),
            "--specpath", str(staging),
            "--add-data", f"{payload}{os.pathsep}.",
            "--version-file", str(ver_file),
        ]
        if icon.exists():
            cmd += ["--icon", str(icon)]
        cmd.append(str(LANDING / "deploy" / "installer_gui.py"))

        print("构建   正在运行 PyInstaller（约 1–2 分钟）...")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        built = staging / "dist" / "ZhijingInstaller.exe"
        if proc.returncode != 0 or not built.exists():
            tail = (proc.stderr or proc.stdout or "")[-600:]
            print(f"提示   PyInstaller 失败，回退到 IExpress\n{tail}")
            return None

        # 体积兜底：带 CPython + tkinter 的单文件不可能只有几百 KB。
        # 出现过一次"返回 0 但产物是残的"——放行会得到一个双击毫无反应的 exe，
        # 正是我们要消灭的那种故障，所以宁可当成失败。
        if built.stat().st_size < 3 * 1024 * 1024:
            tail = (proc.stderr or proc.stdout or "")[-800:]
            print(f"提示   PyInstaller 产物异常小（{built.stat().st_size} 字节），回退到 IExpress\n{tail}")
            return None

        final = DIST / f"智鲸AI客户端-{VERSION}-安装.exe"
        if final.exists():
            final.unlink()
        shutil.move(str(built), str(final))

        # ★ 绝对不要对 PyInstaller 的 onefile 产物跑 rcedit。
        #   它会重写整个 PE 资源段，把追加在文件尾部的 PKG 归档（CPython + 依赖）
        #   一起丢掉：实测 12.7 MB 被裁成 338 KB，产出一个双击毫无反应的死 exe。
        #   版本信息改用 --version-file 交给 PyInstaller 自己写（见上面的 cmd）。
        size = final.stat().st_size
        if size < 3 * 1024 * 1024:
            print(f"提示   产物疑似损坏（{size} 字节），回退到 IExpress")
            return None
        return final
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def make_exe(zip_path: Path, pkg: Path) -> Path | None:
    if not IEXPRESS.exists():
        print("未找到 IExpress，跳过 exe")
        return None
    staging = Path(tempfile.mkdtemp(prefix="zj-client-"))
    try:
        # 载荷 zip 的**根目录直接是文件**：安装脚本把它整个解到目标目录即可。
        payload = staging / "payload.zip"
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for f in sorted(pkg.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(pkg).as_posix())

        (staging / "setup.cmd").write_bytes(SETUP_CMD.replace("\n", "\r\n").encode("ascii"))
        (staging / "done.vbs").write_bytes(b"\xff\xfe" + DONE_VBS.replace("\n", "\r\n").encode("utf-16-le"))

        # 启动项单独打一个包：中文文件名只存在于 zip 条目里（带 UTF-8 标志位），
        # 安装时 tar 解到桌面/开始菜单即可，批处理里一个中文字都不出现。
        with zipfile.ZipFile(staging / "launcher.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("智鲸 AI 客户端.cmd", LAUNCHER_CMD.replace("\n", "\r\n").encode("ascii"))

        build_target = staging / "out.exe"
        # SED 是 ANSI 文件（iexpress 不认 UTF-8）。中文只能按系统 ANSI 码页写，
        # 中文 Windows 是 GBK；其它语言环境会编码失败，退回纯 ASCII 的英文提示，
        # 不让一个提示文案把构建搞挂。
        sed_text = sed(build_target, staging).replace("\n", "\r\n")
        try:
            sed_bytes = sed_text.encode("gbk")
        except UnicodeEncodeError:
            sed_text = sed_text.replace(
                "安装完成！桌面上已创建「智鲸 AI 客户端」，双击即可启动。",
                "Installation complete. A shortcut has been created on your Desktop.",
            )
            sed_bytes = sed_text.encode("ascii", errors="replace")
        (staging / "b.sed").write_bytes(sed_bytes)
        proc = subprocess.run(
            [str(IEXPRESS), "/N", "/Q", str(staging / "b.sed")],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0 or not build_target.exists():
            print(f"IExpress 失败（{proc.returncode}）")
            return None

        final = DIST / f"智鲸AI-客户端-{VERSION}-win-x64.exe"
        if final.exists():
            final.unlink()
        shutil.move(str(build_target), str(final))

        rcedit = next((p for p in (ROOT / "tools").glob("rcedit*.exe")), None)
        icon = LANDING / "deploy" / "app.ico"
        if rcedit and icon.exists():
            subprocess.run(
                [str(rcedit), str(final), "--set-icon", str(icon),
                 "--set-file-version", f"{VERSION}.0",
                 "--set-version-string", "ProductName", "智鲸 AI 客户端",
                 "--set-version-string", "CompanyName", "智鲸 AI"],
                capture_output=True, text=True,
            )

        # 面向用户的下载包：zip 里放 exe + 说明（见 pack_installer）
        return final, pack_installer(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="构建智鲸 AI 客户端安装包")
    ap.add_argument("--no-exe", action="store_true")
    args = ap.parse_args()

    DIST.mkdir(exist_ok=True)
    pkg = assemble()

    files = [f for f in pkg.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    print(f"目录   {pkg.relative_to(ROOT)}   {len(files)} 个文件   {total / 1024 / 1024:.1f} MB")

    z = make_zip(pkg)
    print(f"zip    {z.name}   {z.stat().st_size / 1024 / 1024:.1f} MB   sha256 {sha256(z)[:16]}")

    if not args.no_exe:
        # 首选：PyInstaller 图形安装器（有窗口、有进度、能验证）
        final = make_gui_exe(pkg)
        setup_zip = None
        if final:
            setup_zip = pack_installer(final)
        else:
            # 兜底：IExpress（无界面，用户可能感知不到，但不至于没有产物）
            built = make_exe(z, pkg)
            if built:
                final, setup_zip = built
        if final and setup_zip:
            print(f"exe    {final.name}   {final.stat().st_size / 1024 / 1024:.1f} MB   sha256 {sha256(final)[:16]}")
            print(f"下载包 {setup_zip.name}   {setup_zip.stat().st_size / 1024 / 1024:.1f} MB   sha256 {sha256(setup_zip)[:16]}")


if __name__ == "__main__":
    main()
