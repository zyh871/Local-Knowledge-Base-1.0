# 智鲸 AI · 产品介绍页 卸载脚本
#
# 移除安装目录、桌面与开始菜单快捷方式、以及「应用和功能」里的注册项。
# 只清理本产品自己写入的东西，不动任何其它文件。

[CmdletBinding()]
param([switch]$Force)

$ErrorActionPreference = 'Stop'

# 输出按 UTF-8 走，避免重定向 / 管道时中文变乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$AppName = '智鲸 AI 产品介绍页'
$AppId   = 'ZhijingAI-Landing'

$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"

# 安装位置以注册表记录的为准；没有记录（比如直接解压后运行）才回退到脚本所在目录。
# 早期版本用「是否等于默认路径」来判断，导致装到自定义目录后永远删不掉。
$Target = $null
if (Test-Path $regPath) {
    $recorded = (Get-ItemProperty -Path $regPath -Name 'InstallLocation' -ErrorAction SilentlyContinue).InstallLocation
    if (-not [string]::IsNullOrWhiteSpace($recorded)) { $Target = $recorded }
}
if ([string]::IsNullOrWhiteSpace($Target)) { $Target = $PSScriptRoot }

function Write-Step($text) { Write-Host "  $text" }
function Write-Ok($text)   { Write-Host "  [OK] $text" -ForegroundColor Green }
function Write-Warn2($text){ Write-Host "  [!]  $text" -ForegroundColor Yellow }

Write-Host ''
Write-Host "  卸载 $AppName" -ForegroundColor Cyan
Write-Host '  ────────────────────────────────────────'
Write-Step "安装目录：$Target"

if (-not $Force) {
    $answer = Read-Host '  确认卸载？(y/N)'
    if ($answer -notmatch '^[Yy]') {
        Write-Host '  已取消'
        exit 0
    }
}

try {
    # ── 快捷方式 ────────────────────────────────────────────
    $links = @(
        (Join-Path ([Environment]::GetFolderPath('Desktop'))  "$AppName.lnk"),
        (Join-Path ([Environment]::GetFolderPath('Programs')) "$AppName.lnk")
    )
    foreach ($lnk in $links) {
        if (Test-Path $lnk) {
            Remove-Item $lnk -Force
            Write-Ok "已删除快捷方式 $lnk"
        }
    }

    # ── 注册项 ──────────────────────────────────────────────
    $regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppId"
    if (Test-Path $regPath) {
        Remove-Item $regPath -Recurse -Force
        Write-Ok '已移除卸载注册项'
    }

    # ── 安装目录 ────────────────────────────────────────────
    # 只在「确认这是我们自己的安装目录」时才递归删除：
    # 目录里必须同时有 index.html 与 uninstall.cmd，且不能是盘根或用户目录。
    $full = [System.IO.Path]::GetFullPath($Target)

    $looksLikeOurs = (Test-Path (Join-Path $full 'index.html')) -and
                     (Test-Path (Join-Path $full 'uninstall.cmd'))
    $isDangerous   = ($full -match '^[A-Za-z]:\\?$') -or
                     ($full.TrimEnd('\') -eq [System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\'))

    if ($isDangerous) {
        Write-Warn2 "拒绝删除系统级目录：$full"
    }
    elseif (-not $looksLikeOurs) {
        Write-Warn2 "该目录不像本产品的安装目录，已跳过删除：$full"
        Write-Step '如确认要清理，请手动删除。'
    }
    elseif (Test-Path $full) {
        Remove-Item $full -Recurse -Force
        Write-Ok '安装目录已删除'
    }

    Write-Host ''
    Write-Host '  卸载完成' -ForegroundColor Green
    Write-Host ''
}
catch {
    Write-Host ''
    Write-Host "  卸载过程出错：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
