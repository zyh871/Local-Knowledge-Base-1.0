# 智鲸 AI · 产品介绍页 安装脚本
#
# 由 install.cmd 调用，不直接双击运行。
# 简体中文提示依赖 UTF-8 BOM（打包脚本会写入），否则 PowerShell 5.1 会按 GBK 解码成乱码。

[CmdletBinding()]
param(
    [string]$Target = '',
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

# 输出按 UTF-8 走：PowerShell 5.1 默认用系统 ANSI(GBK) 写重定向输出，
# 一旦输出被管道接到别的程序或写进日志，中文就会变成乱码。
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$AppName    = '智鲸 AI 产品介绍页'
$AppId      = 'ZhijingAI-Landing'
$AppVersion = '1.0.0'

function Write-Step($text) { Write-Host "  $text" }
function Write-Ok($text)   { Write-Host "  [OK] $text"   -ForegroundColor Green }
function Write-Warn2($text){ Write-Host "  [!]  $text"   -ForegroundColor Yellow }

try {
    # ── 目标目录：装到当前用户下，不需要管理员权限 ──────────────
    if ([string]::IsNullOrWhiteSpace($Target)) {
        $Target = Join-Path $env:LOCALAPPDATA $AppId
    }

    $source = $PSScriptRoot

    if (-not (Test-Path (Join-Path $source 'index.html'))) {
        throw "安装包不完整：$source 下找不到 index.html"
    }

    Write-Host ''
    Write-Host "  $AppName · v$AppVersion" -ForegroundColor Cyan
    Write-Host "  ────────────────────────────────────────"
    Write-Step "安装位置：$Target"

    # ── 复制文件 ────────────────────────────────────────────
    if (Test-Path $Target) {
        Write-Step '检测到旧版本，覆盖安装…'
    }
    New-Item -ItemType Directory -Force -Path $Target | Out-Null

    $payload = @(
        'index.html',
        'README.md',
        'VERSION.txt',
        'start.cmd',
        'start.ps1',
        'uninstall.cmd',
        'uninstall.ps1'
    )

    foreach ($item in $payload) {
        $from = Join-Path $source $item
        if (Test-Path $from) {
            Copy-Item $from -Destination $Target -Force
        }
        else {
            Write-Warn2 "缺少文件 $item（已跳过）"
        }
    }

    # deploy 子目录：本地服务器与生产部署配置
    $deploySrc = Join-Path $source 'deploy'
    if (Test-Path $deploySrc) {
        $deployDst = Join-Path $Target 'deploy'
        New-Item -ItemType Directory -Force -Path $deployDst | Out-Null
        Copy-Item (Join-Path $deploySrc '*') -Destination $deployDst -Force -Recurse
    }

    $indexPath = Join-Path $Target 'index.html'
    if (-not (Test-Path $indexPath)) { throw '复制后仍找不到 index.html，安装中止' }
    Write-Ok '文件已复制'

    # ── 快捷方式：直接指向 index.html，用默认浏览器打开 ────────
    # 不指向 start.cmd，是因为 .lnk 指向 .html 时会自动继承浏览器的图标，
    # 比一个黑框命令行图标体面得多，也不依赖猜浏览器路径。
    $shell = New-Object -ComObject WScript.Shell

    $shortcutTargets = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop'))        "$AppName.lnk"),
        (Join-Path ([Environment]::GetFolderPath('Programs'))       "$AppName.lnk")
    )

    $made = 0
    foreach ($lnkPath in $shortcutTargets) {
        try {
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath       = $indexPath
            $lnk.WorkingDirectory = $Target
            $lnk.Description      = '智鲸 AI 桌面版产品介绍页'
            $lnk.Save()
            $made++
        }
        catch {
            Write-Warn2 "创建快捷方式失败：$lnkPath"
        }
    }
    if ($made -gt 0) { Write-Ok "已创建 $made 个快捷方式（桌面 / 开始菜单）" }

    # ── 卸载入口：写进「应用和功能」，让用户能找到它 ─────────────
    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
    try {
        New-Item -Path $regPath -Force | Out-Null
        Set-ItemProperty -Path $regPath -Name 'DisplayName'     -Value $AppName
        Set-ItemProperty -Path $regPath -Name 'DisplayVersion'  -Value $AppVersion
        Set-ItemProperty -Path $regPath -Name 'InstallLocation' -Value $Target
        Set-ItemProperty -Path $regPath -Name 'Publisher'       -Value '智鲸 AI'
        Set-ItemProperty -Path $regPath -Name 'NoModify'        -Value 1 -Type DWord
        Set-ItemProperty -Path $regPath -Name 'NoRepair'        -Value 1 -Type DWord
        Set-ItemProperty -Path $regPath -Name 'UninstallString' `
            -Value "`"$(Join-Path $Target 'uninstall.cmd')`""
        Write-Ok '已注册卸载入口'
    }
    catch {
        Write-Warn2 '注册卸载入口失败（不影响使用，可手动运行 uninstall.cmd）'
    }

    Write-Host ''
    Write-Host '  安装完成' -ForegroundColor Green
    Write-Host ''
    Write-Step "桌面和开始菜单里已有「$AppName」，双击即可打开。"
    Write-Step "需要本地网址（http://localhost）时，运行："
    Write-Step "    $(Join-Path $Target 'start.cmd') -Server"
    Write-Step "卸载：$(Join-Path $Target 'uninstall.cmd')"
    Write-Host ''

    if (-not $Quiet) {
        $answer = Read-Host '  现在打开页面？(Y/n)'
        if ($answer -eq '' -or $answer -match '^[Yy]') {
            Start-Process $indexPath
        }
    }
}
catch {
    Write-Host ''
    Write-Host "  安装失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host '  可尝试：把安装包解压到普通目录（如桌面）后重新运行 install.cmd' -ForegroundColor DarkGray
    exit 1
}
