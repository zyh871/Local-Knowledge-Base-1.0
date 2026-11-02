<#
  配置 Cloudflare 命名隧道（固定域名，免费）。
  前置：一个已在 Cloudflare 托管的域名（域名的 NS 指向 Cloudflare）。
  用法：powershell -ExecutionPolicy Bypass -File setup-named-tunnel.ps1 -Domain example.com -Subdomain kbqa
        生成 https://kbqa.example.com -> 本机 127.0.0.1:4173
  首次会打开浏览器要求登录并授权该域名（只需一次）。
#>
param(
  [Parameter(Mandatory = $true)][string]$Domain,
  [string]$Subdomain = 'kbqa',
  [string]$TunnelName = 'kbqa'
)
$ErrorActionPreference = 'Stop'
$cf = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
$hostname = if ($Subdomain) { "$Subdomain.$Domain" } else { $Domain }
$dir = Join-Path $env:USERPROFILE '.cloudflared'
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }

if (-not (Test-Path (Join-Path $dir 'cert.pem'))) {
  Write-Host "即将打开浏览器：登录 Cloudflare，选择 $Domain 所在账号并授权。" -ForegroundColor Yellow
  & $cf tunnel login
}
if (-not (Test-Path (Join-Path $dir 'cert.pem'))) { throw '未完成 Cloudflare 登录（缺少 cert.pem）。' }

# 创建隧道（若已存在会报错，忽略）
& $cf tunnel create $TunnelName 2>$null | Out-Null
& $cf tunnel route dns $TunnelName $hostname

$cred = Get-ChildItem $dir -Filter "$TunnelName.json" | Select-Object -First 1
if (-not $cred) { throw "未找到隧道凭证 $TunnelName.json" }
$tunnelId = (Get-Content $cred.FullName -Raw | ConvertFrom-Json).TunnelID

$cfg = @"
tunnel: $tunnelId
credentials-file: $($cred.FullName)

ingress:
  - hostname: $hostname
    service: http://127.0.0.1:4173
  - service: http_status:404
"@
Set-Content -Path (Join-Path $dir 'config.yml') -Value $cfg -Encoding UTF8

Write-Host "`n完成：https://$hostname  ->  本机 127.0.0.1:4173" -ForegroundColor Green
Write-Host '之后运行 start.cmd 会自动使用这个命名隧道（固定链接）。' -ForegroundColor Cyan
