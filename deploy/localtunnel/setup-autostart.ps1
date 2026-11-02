<#
  安装“登录自动启动”（无需管理员权限）。
  实现：在用户“启动”文件夹放置 KBQA-AutoStart.vbs，登录时隐藏运行 start.ps1。
  卸载：删除 %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\KBQA-AutoStart.vbs
#>
$ErrorActionPreference = 'Stop'
$start = Join-Path $PSScriptRoot 'start.ps1'
if (-not (Test-Path $start)) { throw "找不到 start.ps1：$start" }

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
if (-not (Test-Path $startup)) { New-Item -ItemType Directory -Path $startup -Force | Out-Null }
$vbs = Join-Path $startup 'KBQA-AutoStart.vbs'

$content = @"
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$start""", 0, False
"@
Set-Content -Path $vbs -Value $content -Encoding ASCII

Write-Host "已安装登录自启（无需管理员）：$vbs" -ForegroundColor Green
Write-Host "取消：删除该 .vbs 文件即可。" -ForegroundColor Cyan
