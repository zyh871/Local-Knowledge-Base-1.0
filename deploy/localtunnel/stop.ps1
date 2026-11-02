$ErrorActionPreference = 'SilentlyContinue'
foreach ($p in 8000, 4173, 5173) {
  Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "已停止端口 $p (pid $($_.OwningProcess))"
  }
}
Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
  Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
  Write-Host "已停止 cloudflared $($_.Id)"
}
Write-Host '全部已停止。' -ForegroundColor Green
