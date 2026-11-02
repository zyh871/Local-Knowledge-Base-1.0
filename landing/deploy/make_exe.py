#!/usr/bin/env python3
"""把安装包 zip 打成真正的单文件 .exe（Windows 自解压安装程序）。

为什么用 IExpress：
    系统里没有 7-Zip，也没有 PyInstaller，而 IExpress（iexpress.exe）是
    Windows 自带组件，能在不引入第三方依赖的前提下产出单文件 exe。
    它不支持子目录，所以载荷是三个平铺文件：
        payload.zip  —— 安装内容（zip 内部保留了 deploy/ 子目录）
        boot.ps1     —— 真正的引导逻辑（解压 → 调 install.ps1 → 打开页面）
        setup.cmd    —— 两行，只为把 boot.ps1 交给 PowerShell 执行

为什么引导逻辑用 PowerShell 而不是批处理：
    在批处理里 `echo x=%ERRORLEVEL%>> log` 会被解析成「重定向文件句柄 N」，
    日志会莫名缺失；`pause` / `Read-Host` 在 IExpress 的无控制台环境下也拿不到
    输入。这些坑不值得在批处理里趟。setup.cmd 保持两行、零重定向。

产物：landing/dist/智鲸AI-产品介绍页-<version>-win-x64.exe

用法：
    python landing/deploy/make_exe.py
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "landing"
DIST = LANDING / "dist"

IEXPRESS = Path(r"C:\Windows\System32\iexpress.exe")

#: PE 资源编辑器。IExpress 生成的 exe 只有默认图标、没有版本信息，
#: 需要用 rcedit 把这些资源写进去。缺这个工具时构建仍然成功，只是图标是默认的。
RCEDIT_CANDIDATES = [
    ROOT / "tools" / "rcedit-x64.exe",
    ROOT / "tools" / "rcedit.exe",
]

ICON = LANDING / "deploy" / "app.ico"
ICON_SOURCE = LANDING / "deploy" / "logo-source.png"


def ensure_icon() -> Path | None:
    """从 logo 源图生成多尺寸 .ico（16→256），供 exe 与快捷方式使用。"""
    if not ICON_SOURCE.exists():
        return None
    try:
        from PIL import Image
    except ImportError:
        print("未安装 Pillow，跳过图标生成")
        return None

    if ICON.exists() and ICON.stat().st_mtime >= ICON_SOURCE.stat().st_mtime:
        return ICON

    src = Image.open(ICON_SOURCE).convert("RGBA")
    bbox = src.getbbox()
    if bbox:
        src = src.crop(bbox)
    # 补成正方形，避免图标在任务栏里被压扁
    w, h = src.size
    side = int(max(w, h) * 1.08)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(src, ((side - w) // 2, (side - h) // 2), src)

    canvas.save(ICON, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"图标   {ICON.name}（16–256 多尺寸）")
    return ICON


def apply_resources(exe_path: Path, version: str) -> None:
    """用 rcedit 写入图标与版本信息。

    没有该工具时只提示、不失败 —— 图标是锦上添花，装不上不该阻断构建。
    """
    rcedit = next((p for p in RCEDIT_CANDIDATES if p.exists()), None)
    if rcedit is None:
        print("提示   未找到 rcedit（tools/rcedit-x64.exe），exe 将使用默认图标")
        return

    icon = ensure_icon()
    args = [
        str(rcedit),
        str(exe_path),
        "--set-file-version", f"{version}.0",
        "--set-product-version", f"{version}.0",
        "--set-version-string", "ProductName", "智鲸 AI 产品介绍页",
        "--set-version-string", "FileDescription", "智鲸 AI 产品介绍页安装程序",
        "--set-version-string", "CompanyName", "智鲸 AI",
        "--set-version-string", "LegalCopyright", "© 2026 智鲸 AI",
        "--set-version-string", "OriginalFilename", exe_path.name,
    ]
    if icon is not None:
        args += ["--set-icon", str(icon)]

    try:
        proc = subprocess.run(args, capture_output=True, text=True)
    except OSError as exc:
        # 典型：rcedit 下载被截断，Windows 报 WinError 193“不是有效的 Win32 应用程序”
        print(f"提示   rcedit 无法执行（{exc.strerror or exc}），exe 将保留默认图标")
        return
    if proc.returncode != 0:
        print(f"提示   rcedit 执行失败（{proc.returncode}）：{proc.stderr.strip()[:160]}")
        return
    print(f"资源   已写入图标{'与版本信息' if icon else '，版本信息已写入'}")

#: 只为把控制权交给 PowerShell。全部 ASCII：.cmd 在中文 Windows 下按 OEM
#: 代码页解析，写中文会变乱码；中文提示统一由 boot.ps1 / install.ps1 负责。
SETUP_CMD = r"""@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0boot.ps1"
exit /b %ERRORLEVEL%
"""

#: 引导脚本。写出时加 UTF-8 BOM —— PowerShell 5.1 对无 BOM 的 UTF-8 会按
#: 系统 ANSI（GBK）解码，所有中文提示都会变成乱码。
#:
#: ★ 这里**不复用 install.ps1，而是把安装动作内联**，原因：
#:   IExpress 拉起 AppLaunched 时没有控制台，而 install.ps1 用 Write-Host 输出。
#:   实测在无控制台环境下「& powershell -File install.ps1」走到一半就静默终止
#:   （日志停在调用前一行），既不报错也不落地。改为单进程 + 只写文件日志后稳定。
#:   步骤与 install.ps1 一致（复制→快捷方式→注册表→打开），改动时两边都要改。
BOOT_PS1 = r"""# 智鲸 AI 产品介绍页 · 单文件安装程序引导逻辑
#
# 流程：解压 payload.zip → 复制到 LOCALAPPDATA → 建快捷方式 → 写卸载项 → 打开页面
# 全程无控制台交互：进度写日志文件，失败弹消息框。

$ErrorActionPreference = 'Stop'

$AppName = '智鲸 AI 产品介绍页'
$AppId   = 'ZhijingAI-Landing'
$Version = '1.0.0'

$log = Join-Path $env:TEMP 'zhijing-install.log'

function Write-Log([string]$msg) {
    try {
        Add-Content -LiteralPath $log -Value ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg) -Encoding UTF8
    } catch { }
}

function Show-Fail([string]$msg) {
    try {
        Add-Type -AssemblyName PresentationFramework
        [System.Windows.MessageBox]::Show(
            "安装失败。`n`n$msg`n`n详细日志：$log",
            $AppName
        ) | Out-Null
    } catch { }
}

try {
    $here = $PSScriptRoot
    $pkg  = Join-Path $here '_payload'
    Write-Log "here=$here"

    Write-Log 'expanding payload.zip'
    Expand-Archive -LiteralPath (Join-Path $here 'payload.zip') -DestinationPath $pkg -Force

    if (-not (Test-Path (Join-Path $pkg 'index.html'))) {
        throw "解压后缺少 index.html（$pkg）"
    }

    # ── 1. 复制到用户目录（不需要管理员） ──────────────────────
    $target = Join-Path $env:LOCALAPPDATA $AppId
    Write-Log "target=$target"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item (Join-Path $pkg '*') -Destination $target -Recurse -Force

    $indexPath = Join-Path $target 'index.html'
    if (-not (Test-Path $indexPath)) { throw '复制后仍找不到 index.html' }
    Write-Log 'files copied'

    # ── 2. 快捷方式（直接指向 index.html，继承浏览器图标） ─────────
    $shell = New-Object -ComObject WScript.Shell
    $made = 0
    foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        try {
            $lnk = $shell.CreateShortcut((Join-Path $dir "$AppName.lnk"))
            $lnk.TargetPath       = $indexPath
            $lnk.WorkingDirectory = $target
            $lnk.Description      = '智鲸 AI 桌面版产品介绍页'
            $lnk.Save()
            $made++
        } catch {
            Write-Log "shortcut failed: $($_.Exception.Message)"
        }
    }
    Write-Log "shortcuts=$made"

    # ── 3. 卸载入口 ────────────────────────────────────────────
    try {
        $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
        New-Item -Path $regPath -Force | Out-Null
        Set-ItemProperty -Path $regPath -Name 'DisplayName'     -Value $AppName
        Set-ItemProperty -Path $regPath -Name 'DisplayVersion'  -Value $Version
        Set-ItemProperty -Path $regPath -Name 'InstallLocation' -Value $target
        Set-ItemProperty -Path $regPath -Name 'Publisher'       -Value '智鲸 AI'
        Set-ItemProperty -Path $regPath -Name 'NoModify'        -Value 1 -Type DWord
        Set-ItemProperty -Path $regPath -Name 'NoRepair'        -Value 1 -Type DWord
        Set-ItemProperty -Path $regPath -Name 'UninstallString' `
            -Value "`"$(Join-Path $target 'uninstall.cmd')`""
        Write-Log 'registry ok'
    } catch {
        Write-Log "registry failed: $($_.Exception.Message)"
    }

    # ── 4. 打开页面 ────────────────────────────────────────────
    Start-Process $indexPath
    Write-Log 'done'
    exit 0
}
catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    Show-Fail $_.Exception.Message
    exit 1
}
"""


def sed_content(target: Path, src_dir: Path, friendly: str) -> str:
    """IExpress 的构建描述文件。

    它是「模板 + 变量表」结构：[Options] 里一律写 %XXX% 占位符，真实取值放在
    [Strings]。直接把字面值写进 [Options] 会被忽略或直接构建失败。

    几个静默失败点（iexpress 返回 1 但不打印任何原因）：
      * [Options] 漏掉 `SourceFiles=SourceFiles`，载荷表就没人引用；
      * `SourceFiles0`（目录）必须带结尾反斜杠 —— iexpress 是字符串直接拼接，
        少了它会去找 `C:\\...\\zj-sfx-xxxpayload.zip`；
      * `%FILE0%` 必须在 [Strings] 里有对应 `FILE0="真实文件名"`；
      * `TargetName` 必须是绝对路径；
      * SED 必须是 CRLF，且内容为纯 ASCII。
    """
    src = str(src_dir)
    if not src.endswith("\\"):
        src += "\\"

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
FriendlyName={friendly}
AppLaunched=setup.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="payload.zip"
FILE1="setup.cmd"
FILE2="boot.ps1"
[SourceFiles]
SourceFiles0={src}
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
"""


def main() -> None:
    ap = argparse.ArgumentParser(description="生成单文件 .exe 安装程序")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--keep-temp", action="store_true", help="保留临时目录便于排查")
    args = ap.parse_args()

    if not IEXPRESS.exists():
        raise SystemExit(f"找不到 IExpress：{IEXPRESS}")

    src_zip = DIST / f"zhijing-landing-{args.version}-windows.zip"
    if not src_zip.exists():
        raise SystemExit(f"先运行 package.py 生成 {src_zip.name}")

    exe_name = f"智鲸AI-产品介绍页-{args.version}-win-x64.exe"
    exe_path = DIST / exe_name
    if exe_path.exists():
        exe_path.unlink()

    staging = Path(tempfile.mkdtemp(prefix="zj-sfx-"))
    try:
        # 载荷 zip 的**根目录直接是文件**（index.html、install.ps1、deploy/…）。
        # 发行版 zip 外面套了一层同名文件夹，对人工解压友好，但在引导脚本里
        # 就得多一步「在解压结果里找子目录」，是无谓的失败点。
        bundle_dir = DIST / f"zhijing-landing-{args.version}-windows"
        if not bundle_dir.exists():
            with zipfile.ZipFile(src_zip) as zf:
                zf.extractall(DIST)

        payload = staging / "payload.zip"
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for f in sorted(bundle_dir.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(bundle_dir).as_posix())

        # 三个载荷文件一律 CRLF；boot.ps1 额外加 UTF-8 BOM
        (staging / "setup.cmd").write_bytes(
            SETUP_CMD.replace("\n", "\r\n").encode("ascii")
        )
        (staging / "boot.ps1").write_bytes(
            b"\xef\xbb\xbf" + BOOT_PS1.replace("\n", "\r\n").encode("utf-8")
        )

        build_target = staging / "out.exe"
        sed_text = sed_content(build_target, staging, "Zhijing AI Product Landing Page")
        (staging / "build.sed").write_bytes(sed_text.replace("\n", "\r\n").encode("ascii"))

        proc = subprocess.run(
            [str(IEXPRESS), "/N", "/Q", str(staging / "build.sed")],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)
            raise SystemExit(f"IExpress 构建失败，退出码 {proc.returncode}")

        if not build_target.exists():
            raise SystemExit("IExpress 返回成功，但没有生成 exe")

        shutil.move(str(build_target), str(exe_path))

        # 图标与版本信息必须在 exe 落到最终位置之后写，否则 rcedit 改的是临时文件
        apply_resources(exe_path, args.version)

        raw = exe_path.read_bytes()
        print(f"产物   {exe_path.name}")
        print(f"大小   {len(raw) / 1024:.1f} KB")
        print(f"sha256 {hashlib.sha256(raw).hexdigest()}")
        print(f"载荷   payload.zip {payload.stat().st_size / 1024:.1f} KB + setup.cmd + boot.ps1")
    finally:
        if args.keep_temp:
            print(f"临时目录保留在：{staging}")
        else:
            shutil.rmtree(staging, ignore_errors=True)


if __name__ == "__main__":
    main()
