# 智鲸 AI · 产品介绍页 启动脚本
#
#   start.cmd                 用默认浏览器直接打开页面（零依赖，推荐）
#   start.cmd -Server         起一个本地 HTTP 服务，然后打开 http://localhost:8080
#   start.cmd -Server -Port 9000
#   start.cmd -NoBrowser      只起服务、不打开浏览器（排查用）
#
# -Server 模式用 TcpListener 自己实现最小 HTTP/1.1，
# 不依赖 Python、Node 或管理员权限（HttpListener 需要 URL ACL，普通用户会 403）。

[CmdletBinding()]
param(
    [switch]$Server,
    [switch]$NoBrowser,
    [int]$Port = 8080
)

$ErrorActionPreference = 'Stop'

# 输出按 UTF-8 走，避免重定向 / 管道时中文变乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$root  = $PSScriptRoot
$index = Join-Path $root 'index.html'

if (-not (Test-Path $index)) {
    Write-Host "  找不到 index.html：$index" -ForegroundColor Red
    Write-Host '  安装可能不完整，请重新运行 install.cmd' -ForegroundColor DarkGray
    exit 1
}

# ── 默认模式：直接交给默认浏览器 ───────────────────────────
if (-not $Server) {
    if (-not $NoBrowser) {
        Start-Process $index
    }
    return
}

# ── 本地 HTTP 服务模式 ────────────────────────────────────
#: 扩展名 → Content-Type。
# 名字必须是 MimeMap：PowerShell 变量名不区分大小写，若叫 $Mime，
# 后面 `$mime = 'text/plain'` 会把整张表覆盖成字符串，ContainsKey 就会报
# 「[System.String] 不包含名为 ContainsKey 的方法」。
$MimeMap = @{
    '.html' = 'text/html; charset=utf-8'
    '.htm'  = 'text/html; charset=utf-8'
    '.css'  = 'text/css; charset=utf-8'
    '.js'   = 'application/javascript; charset=utf-8'
    '.png'  = 'image/png'
    '.jpg'  = 'image/jpeg'
    '.svg'  = 'image/svg+xml'
    '.ico'  = 'image/x-icon'
    '.txt'  = 'text/plain; charset=utf-8'
    '.md'   = 'text/markdown; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
}

function Get-FreePort([int]$start) {
    for ($p = $start; $p -lt $start + 20; $p++) {
        $probe = $null
        try {
            $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $p)
            $probe.Start()
            $probe.Stop()
            return $p
        }
        catch {
            if ($probe) { try { $probe.Stop() } catch { } }
        }
    }
    throw "端口 $start–$($start + 19) 都被占用，请用 -Port 指定"
}

$port = Get-FreePort $Port
$url  = "http://127.0.0.1:$port/"

$listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
$listener.Start()

Write-Host ''
Write-Host '  智鲸 AI · 产品介绍页' -ForegroundColor Cyan
Write-Host "  目录  $root"
Write-Host "  地址  $url" -ForegroundColor Green
Write-Host '  按 Ctrl+C 停止'
Write-Host ''

if (-not $NoBrowser) {
    Start-Process $url
}

$rootFull = [System.IO.Path]::GetFullPath($root).TrimEnd('\')

try {
    while ($true) {
        $client = $listener.AcceptTcpClient()
        try {
            $client.ReceiveTimeout = 5000
            $stream = $client.GetStream()
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::ASCII)
            $requestLine = $reader.ReadLine()

            if ([string]::IsNullOrWhiteSpace($requestLine)) { continue }

            # 把请求头读干净，否则某些客户端会因为缓冲区未清空而卡住
            while ($true) {
                $line = $reader.ReadLine()
                if ($null -eq $line -or $line -eq '') { break }
            }

            $parts = $requestLine.Split(' ')
            $rawPath = if ($parts.Length -ge 2) { $parts[1] } else { '/' }
            $rawPath = ($rawPath -split '\?')[0]
            if ($rawPath -eq '/' -or $rawPath -eq '') { $rawPath = '/index.html' }

            $rel = [System.Uri]::UnescapeDataString($rawPath.TrimStart('/'))
            $candidate = Join-Path $rootFull $rel.Replace('/', '\')

            $status = '200 OK'
            $body   = $null
            $mime   = 'application/octet-stream'

            # 逐段判断、每段单独 try：之前把「解析路径 / 读文件 / 定类型」包在一个
            # try 里，任何一步抛错都会让 status 变成 404，而 body 已经读到内存了 ——
            # 结果就是「内容是对的，状态码和 Content-Type 是错的」，浏览器会把页面
            # 当成文件下载。分段之后，出错只影响出错的那一段。
            $full = $null
            try {
                $full = [System.IO.Path]::GetFullPath($candidate)
            }
            catch {
                $full = $null
            }

            if ($null -eq $full -or -not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
                $status = '403 Forbidden'
            }
            elseif (Test-Path -LiteralPath $full -PathType Leaf) {
                try {
                    $body = [System.IO.File]::ReadAllBytes($full)
                }
                catch {
                    Write-Host "  [!] 读取失败：$full" -ForegroundColor Yellow
                    $status = '500 Internal Server Error'
                }

                if ($null -ne $body) {
                    $ext = [System.IO.Path]::GetExtension($full)
                    if (-not [string]::IsNullOrEmpty($ext)) {
                        $ext = $ext.ToLowerInvariant()
                        if ($MimeMap.ContainsKey($ext)) { $mime = $MimeMap[$ext] }
                    }
                }
            }
            else {
                $status = '404 Not Found'
            }

            if ($null -eq $body) {
                $body = [System.Text.Encoding]::UTF8.GetBytes("$status`n")
                $mime = 'text/plain; charset=utf-8'
            }

            $head = "HTTP/1.1 $status`r`n" +
                    "Content-Type: $mime`r`n" +
                    "Content-Length: $($body.Length)`r`n" +
                    "Cache-Control: no-store`r`n" +
                    "X-Content-Type-Options: nosniff`r`n" +
                    "Connection: close`r`n`r`n"

            $headBytes = [System.Text.Encoding]::ASCII.GetBytes($head)
            $stream.Write($headBytes, 0, $headBytes.Length)
            $stream.Write($body, 0, $body.Length)
            $stream.Flush()
        }
        catch {
            # 单个连接出错不该拖垮整个服务（浏览器预连接、提前断开都很常见）。
            # 但一定要留下痕迹，否则「连接被关闭、页面空白」会完全没有线索。
            $logLine = "{0}  {1}`r`n  {2}" -f (Get-Date -Format 'HH:mm:ss'), $_.Exception.Message, $_.InvocationInfo.PositionMessage
            Write-Host $logLine -ForegroundColor Yellow
            try {
                Add-Content -LiteralPath (Join-Path $root 'server-error.log') -Value $logLine -Encoding UTF8
            }
            catch { }
        }
        finally {
            try { $client.Close() } catch { }
        }
    }
}
finally {
    $listener.Stop()
    Write-Host '  服务已停止'
}
