<#
  一键启动：后端 FastAPI(:8000) + 前端生产预览(:4173) + cloudflared 隧道
  - 有 %USERPROFILE%\.cloudflared\config.yml -> 命名隧道（固定域名）
  - 否则 -> Quick Tunnel（临时随机地址；启动后写入本目录 url.txt 便于查看）
  Tailscale 已弃用，不再参与。
  用法：双击 start.cmd；或 powershell -ExecutionPolicy Bypass -File start.ps1
#>
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cf   = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$backendPort = 8000
$previewPort = 4173

function Test-Port([int]$p) {
  # 用真实 TCP 连接判断端口是否有服务（Get-NetTCPConnection 在本机对某些进程会 pid=0 误报）
  $c = New-Object System.Net.Sockets.TcpClient
  try { $c.Connect('127.0.0.1', $p); return $true } catch { return $false } finally { $c.Dispose() }
}

# 1) 没有构建产物先构建
if (-not (Test-Path "$root\frontend\dist\index.html")) {
  Write-Host '[build] 未找到 frontend/dist，先构建...' -ForegroundColor Yellow
  Push-Location "$root\frontend"; & cmd.exe /c 'npm run build'; Pop-Location
}

# 2) 后端
if (Test-Port $backendPort) {
  Write-Host "[skip] backend :$backendPort 已在运行"
} else {
  Start-Process -FilePath "$root\backend\venv\Scripts\python.exe" `
    -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$backendPort" `
    -WorkingDirectory "$root\backend" -WindowStyle Hidden
  Write-Host "[ok] backend :$backendPort 已启动" -ForegroundColor Green
}

# 3) 前端生产预览
if (Test-Port $previewPort) {
  Write-Host "[skip] preview :$previewPort 已在运行"
} else {
  Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm run preview' `
    -WorkingDirectory "$root\frontend" -WindowStyle Hidden
  Write-Host "[ok] preview :$previewPort 已启动" -ForegroundColor Green
}

# 4) cloudflared 隧道（清空失效的系统代理：本机 HTTP(S)_PROXY 常指向已关闭的端口）
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'
Start-Sleep -Seconds 3

if (Get-Process cloudflared -ErrorAction SilentlyContinue) {
  Write-Host '[skip] cloudflared 已在运行'
} else {
  $cfg = Join-Path $env:USERPROFILE '.cloudflared\config.yml'
  if (Test-Path $cfg) {
    Start-Process -FilePath $cf -ArgumentList 'tunnel','run' -WindowStyle Hidden
    Write-Host '[ok] cloudflared 命名隧道已启动（固定域名）' -ForegroundColor Green
  } else {
    $log = Join-Path $PSScriptRoot 'cloudflared.log'
    Start-Process -FilePath $cf `
      -ArgumentList 'tunnel','--url',"http://127.0.0.1:$previewPort",'--no-autoupdate','--logfile',$log `
      -WindowStyle Hidden
    # 等待并解析出本次的 trycloudflare 地址，写入 url.txt
    $url = ''
    for ($i = 0; $i -lt 20; $i++) {
      Start-Sleep -Seconds 1
      $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction SilentlyContinue | Select-Object -Last 1
      if ($m) { $url = $m.Matches[0].Value; break }
    }
    if ($url) {
      Set-Content -Path (Join-Path $PSScriptRoot 'url.txt') -Value $url -Encoding ASCII
      Write-Host "[ok] 公网地址: $url" -ForegroundColor Green
      Write-Host '     （临时地址，重启会变；已写入本目录 url.txt）' -ForegroundColor Yellow
    } else {
      Write-Host '[warn] 暂未取到公网地址，请查看本目录 cloudflared.log' -ForegroundColor Yellow
    }
  }
}

Write-Host "`n本地：http://127.0.0.1:$previewPort   API：http://127.0.0.1:$backendPort" -ForegroundColor Cyan
