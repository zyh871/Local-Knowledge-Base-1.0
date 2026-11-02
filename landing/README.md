# 智鲸 AI · 产品介绍页

产品介绍页 + 开发者文档，另附安装包与部署配置。

- `index.html` —— 介绍页。CSS / JS / logo / favicon 全部内联，**不请求任何外部资源**
  （无 CDN、无字体外链、无图床）。唯一的例外是 `shots/` 里的**真实界面截图**
  （webp + png 两版，共约 1 MB）：把页面单独复制走时要连 `shots/` 一起带，
  否则首屏那张截图会破图。
- `developers.html` —— 开发者文档：项目介绍、技术栈、目录结构、使用说明、
  配置项、接口约定、贡献指南。与介绍页共用同一套设计 token，同样没有外部依赖。

两张图都是跑真实后端截的（`frontend/scripts/capture-shots.mjs`），
不是手绘示意图 —— 这一点是刻意的：画出来的界面一眼就能看出假。

---

## 一、三种使用形态

| 形态 | 适用场景 | 怎么做 |
|------|----------|--------|
| **单文件** | 发给别人看、插 U 盘、离线演示 | 把 `index.html` 复制到任意位置，双击 |
| **单文件 exe** | 发给别人装在自己电脑上 | 双击 `dist/智鲸AI-产品介绍页-*-win-x64.exe` |
| **服务器部署** | 挂到域名、内网统一入口 | Nginx 或 Docker，见下文 |

页面上的下载按钮指向 `download/` 目录，**发布前必须先把安装包复制进去**
（`deploy/` 里的构建脚本不会自动做这一步，见「六、重新打包」）。

---

## 一之二、线上访问：两个 403 的真实原因

发布后的分享链接形如：

```
https://<你的域>/?sharecode=<10 位码>
```

它的访问控制有两层，**任何一层不满足都是 403**，但两种 403 的文案不同，可以据此区分：

| 现象 | 403 页标题 | 原因 | 处理 |
|------|-----------|------|------|
| 少参数 | 「链接不完整」 | 缺 `?sharecode=`，或复制时被截断、**大小写被改**（码区分大小写） | 发完整链接，别手动编辑 |
| 在微信里点开 | 「请在浏览器中打开」 | 服务端主动拦截微信内置浏览器 UA（`MicroMessenger`） | 右上角「…」→「在浏览器打开」 |

实测结论（2026-10-29）：

- 裸域名 `https://<域>/` → **403**；带正确 `sharecode` → 200
- `sharecode` 改成小写 → 403
- `MicroMessenger` UA → **403**（即使链接完整）；Safari / Chrome / QQ / 钉钉 / 支付宝 UA → 200

所以「别人打不开」最常见的就是这两种：**链接被截断**，或者**对方在微信里点的**。
微信这一层拦不住，只能引导对方用系统浏览器打开；`download/share-qr.png` 是同一个链接的二维码，
扫码会直接走系统浏览器，可以绕开这个问题。

---

## 二、Windows 安装

有两种分发形态，内容完全一致，按场合选：

| 形态 | 文件 | 适合 |
|------|------|------|
| **单文件 exe**（推荐） | `智鲸AI-产品介绍页-1.0.0-win-x64.exe`（232 KB） | 发给别人，双击即可 |
| 便携 zip | `zhijing-landing-1.0.0-windows.zip`（46 KB） | 需要看清楚里面有什么、或内网不允许跑 exe |

### 单文件 exe

双击 `智鲸AI-产品介绍页-1.0.0-win-x64.exe`，它会：

1. 自解压到临时目录
2. 把页面装到 `%LOCALAPPDATA%\ZhijingAI-Landing`（**不需要管理员**）
3. 建桌面 + 开始菜单快捷方式，写卸载项
4. 自动用默认浏览器打开页面

全程没有控制台窗口，失败才会弹一个提示框，并留下日志 `%TEMP%\zhijing-install.log`。

> exe 由 Windows 自带的 **IExpress** 打包（见 `deploy/make_exe.py`），不依赖 7-Zip / PyInstaller。
> 因此它**没有代码签名**，首次运行可能被 SmartScreen 拦下：点「更多信息 → 仍要运行」。
> 若要做正式签名，需自备代码签名证书，用 `signtool sign /f cert.pfx /tr <时间戳服务器> <exe>`。

### 便携 zip

解压 `zhijing-landing-1.0.0-windows.zip` 后是一个同名目录：

```
zhijing-landing-1.0.0-windows/
├── index.html          介绍页（约 92 KB）
├── developers.html     开发者文档
├── shots/              真实界面截图（webp + png）
├── install.cmd         安装（双击这个）
├── install.ps1         安装逻辑
├── start.cmd           启动
├── start.ps1           启动逻辑
├── uninstall.cmd       卸载
├── uninstall.ps1       卸载逻辑
├── README.md           本文件
├── VERSION.txt         版本信息
└── deploy/
    ├── serve.py        零依赖静态服务器（需要 Python 3.8+）
    ├── nginx.conf      Nginx 站点配置
    └── Dockerfile      容器镜像
```

### 安装步骤

1. **解压**到任意目录（推荐先解压到桌面，不要直接在压缩包里双击）。
2. **双击 `install.cmd`**。
3. 安装脚本会做四件事：
   - 复制文件到 `%LOCALAPPDATA%\ZhijingAI-Landing`（**不需要管理员权限**）
   - 在**桌面**和**开始菜单**创建快捷方式
   - 在「设置 → 应用 → 已安装的应用」里登记卸载入口
   - 询问是否立刻打开页面（回车即打开）
4. 之后随时双击桌面上的「智鲸 AI 产品介绍页」即可。

> **安装脚本被执行策略拦下？**
> `install.cmd` 内部已经带了 `-ExecutionPolicy Bypass`，正常不会被拦。
> 若组织策略更严格、仍提示「已阻止此脚本」，右键 `install.cmd` → 属性 → 勾选「解除锁定」，或直接在 PowerShell 里运行：
> `powershell -ExecutionPolicy Bypass -File .\install.ps1`

### 启动

```bat
:: 用默认浏览器打开（默认方式，零依赖）
start.cmd

:: 起一个本地 HTTP 服务，然后打开 http://localhost:8080
start.cmd -Server

:: 指定端口
start.cmd -Server -Port 9000
```

`-Server` 模式用 `TcpListener` 自己实现最小 HTTP/1.1，**不需要 Python / Node / 管理员权限**。
（`HttpListener` 在非管理员下会因为缺少 URL ACL 而 403，所以没有采用。）

按 `Ctrl+C` 停止服务。

### 卸载

两种方式，等价：

- 「设置 → 应用 → 已安装的应用」里找到「智鲸 AI 产品介绍页」→ 卸载
- 直接运行安装目录下的 `uninstall.cmd`

卸载会删除安装目录、快捷方式和注册项。**不会**动你放在别处的 `index.html`。

---

## 三、Linux / macOS 部署

单文件，怎么都行：

```bash
# 1. 直接打开
open index.html            # macOS
xdg-open index.html        # Linux

# 2. 起本地服务（Python 3 自带，无需装任何包）
python3 deploy/serve.py                 # http://127.0.0.1:8080
python3 deploy/serve.py --port 9000 --open

# 3. 给别人看（会监听全部网卡，日志里会有提示）
python3 deploy/serve.py --host 0.0.0.0
```

---

## 四、生产部署

### Nginx

```bash
sudo mkdir -p /var/www/zhijing-landing
sudo cp index.html /var/www/zhijing-landing/
sudo cp deploy/nginx.conf /etc/nginx/conf.d/zhijing-landing.conf
# 按需改 root 路径，然后
sudo nginx -t && sudo nginx -s reload
```

配置里已经带好 gzip、`Cache-Control`、CSP 与 `X-Content-Type-Options`。

### Docker

```bash
docker build -t zhijing-landing -f deploy/Dockerfile .
docker run --rm -p 8080:80 zhijing-landing
# 访问 http://localhost:8080
```

镜像基于 `nginx:1.27-alpine`，只 COPY 页面与站点配置，体积约 50 MB。

### 任意静态托管

页面不依赖路由（无 History 模式），也没有任何服务端接口，直接上传 `index.html` 即可：
对象存储、GitHub Pages、Vercel、内网文件服务器都行。

---

## 五、改内容前需要知道的

页面是**手写的单文件**，没有构建步骤。几个容易踩的点：

1. **下载地址集中在一处**：`index.html` 底部脚本里的 `DOWNLOAD_BASE` 常量，所有
   `data-download` 按钮都指向它。改成真实安装包地址即可。
2. **版本号与安装包体积**是占位值（`1.0.0`、96/104/92 MB），发布前按实际构建产物替换。
3. **换 logo**：替换 `deploy/logo-source.png`，再跑 `python deploy/inline_assets.py`。
   脚本会自动裁掉透明留白、补成正方形、生成 base64 写回 `--logo` 变量，favicon 一并换掉。
   脚本是**幂等**的，重复运行结果一致，可以放心反复跑。
4. **中文行宽用 `em` 不要用 `ch`**：`ch` 在中文字体里只有半字宽，用它限制标题宽度会把
   词拆开。
5. **改 `start.ps1` 时注意 PowerShell 变量名不分大小写**：`$Mime` 与 `$mime` 是同一个变量。
   之前用 `$Mime` 存 MIME 表、又用 `$mime` 存单次响应的类型，第二个赋值把整张表覆盖成了
   字符串，`ContainsKey` 直接抛错 —— 表现是「页面内容正确，但状态码 404、类型
   `application/octet-stream`」，浏览器会把页面当成文件下载。表名现在叫 `$MimeMap`。

---

## 六、重新打包

```bash
python landing/deploy/package.py
# 指定版本号
python landing/deploy/package.py --version 1.0.1

# 再把 zip 包成单文件 exe（需先有 zip）
python landing/deploy/make_exe.py --version 1.0.1
```

产物在 `landing/dist/`：

- `zhijing-landing-<version>-windows.zip` — 分包
- `智鲸AI-产品介绍页-<version>-win-x64.exe` — 单文件安装程序
- `manifest.txt` — 逐文件大小与 SHA256 前 16 位，以及 zip 的整体 SHA256
- `zhijing-landing-<version>-windows/` — 解包后的目录，方便本地直接试装

**最后一步不能忘：把产物复制到 `download/`，页面按钮才指向得到文件。**

```bash
cp "landing/dist/智鲸AI-产品介绍页-1.0.0-win-x64.exe" landing/download/zhijing-ai-1.0.0-win-x64.exe
cp landing/dist/zhijing-landing-1.0.0-windows.zip    landing/download/zhijing-ai-landing-1.0.0.zip
```

`download/` 用的是 ASCII 文件名（URL 里中文需要百分号编码，容易出错），
文件名与页面里 `DOWNLOADS` 映射、以及三张平台卡片的「文件」那一行必须一致。
换版本号时这三处要一起改。

### 客户端安装包（`deploy/build_client.py`）

```bash
python landing/deploy/build_client.py     # 约 1 分钟
```

产物在 `landing/dist/`：

| 文件 | 用途 |
|---|---|
| `zhijing-ai-client-<ver>-installer.zip` | **给用户下载的就是这个**：里面是一个 `.exe` + 使用说明 |
| `智鲸AI客户端-<ver>-安装.exe` | zip 里那个图形安装器本体（约 12 MB，PyInstaller） |
| `zhijing-ai-client-<ver>.zip` | 源码包，macOS / Linux 用，也给 `install.cmd` 手动装 |

同步到站点：

```bash
cp landing/dist/zhijing-ai-client-1.0.0-installer.zip landing/download/
cp landing/dist/zhijing-ai-client-1.0.0.zip           landing/download/
cp "landing/dist/智鲸AI-客户端-1.0.0-win-x64.exe"      landing/download/zhijing-ai-client-1.0.0-win-x64.exe
```

#### 安装器：PyInstaller 图形程序（`deploy/installer_gui.py`）

双击 → 弹窗 → **可以自己选安装位置**（默认 `%LOCALAPPDATA%\ZhijingAIClient`，
点「浏览…」或直接改输入框）→ 点「开始安装」→ 进度条 → 完成弹窗里能一键启动。
约 12 MB（带 CPython + tkinter）。

支持静默安装，便于批量部署与自测：

```bat
智鲸AI客户端-1.0.0-安装.exe --silent --dir "D:\智鲸AI"
```

三个安全细节：

- **不误删**：只有目标目录里同时存在 `VERSION.txt` + `start.cmd`（确认是我们自己的旧安装）
  才整体清空；否则只做覆盖式解压，用户已有的文件不动。
- **先验权限**：选目录时就试写一个探针文件，`C:\Program Files` 这类需要管理员的
  位置会立刻提示换目录，不会装到一半才失败。
- **装完顺手生成 `.env`**：`ADMIN_USERNAME/ADMIN_PASSWORD/JWT_SECRET_KEY` 必填，
  并探测本机 Ollama——有对话模型就写 `MODEL_BACKEND=ollama`（零密钥可用），
  没有就留下 `ZHIPU_API_KEY=` 待填。初始密码弹窗里显示一次。

**桌面快捷方式是真 `.lnk`**（`create_shortcuts()`）：桌面与开始菜单各一个，
带 `app.ico` 图标、可右键改属性，启动时不弹黑窗（`WindowStyle=7`）。
早先版本是往桌面丢一个 `.cmd`，图标难看、属性也改不了——别退回去。
`.lnk` 用 PowerShell 的 WScript.Shell 生成（安装器自己是正常进程，起子进程没限制）；
脚本先写成 UTF-8+BOM 的临时 `.ps1` 再执行，避免中文塞进 `-Command` 被代码页搞乱。
建不出来时自动退回 `.cmd`，至少能启动。

> 早期的 IExpress 自解压方案已降级为兜底（`make_exe()`）。它的问题不是配置错，
> 而是那套宿主根本不适合做安装器：`wextract` 拉起的进程**没有控制台**，
> 在里面 PowerShell 做重活会静默死（`Expand-Archive` 崩、`ZipFile` 5 次挂 2 次、
> COM 崩、批量 `Copy-Item` 崩），**任何 `Start-Process`/子进程会被连同宿主一起杀掉**，
> 界面也完全不可见、不可控。用户那边的表现就是「双击 exe，毫无反应」。

#### ★ 千万不要用 rcedit 改 PyInstaller 的 onefile 产物

rcedit 会重写整个 PE 资源段，把**追加在文件尾部的 PKG 归档**（CPython + 全部依赖）
一起丢掉：实测 12.7 MB 被裁成 **338 KB**，产出一个能启动却立刻死掉的 exe
（`--noconsole` 下连报错都没有，双击就是"没反应"）。

版本信息改用 PyInstaller 的 `--version-file` 写（注意 `filevers` 必须是 4 个整数，
写成 `"1.0.0"` 会让它的 deserializer 抛 `ValueError`）。构建脚本里另有体积兜底：
产物 < 3 MB 直接判失败，宁可回退也不放行残废 exe。

#### ★ 下载文件名不能用 `setup.zip` / `win.zip`

站点前面有 WAF（响应头 `Server: stgw`）。`zhijing-ai-client-1.0.0-setup.zip`
和 `*-win.zip` 一律返回 **403「您的请求已中断」**，而**同一份字节**改名成
`*-installer.zip` 就是 200。也就是说拦的是 URL 里的词，不是文件内容
（裸 `.exe`、以及不含 exe 的 zip 都放行）。所以产物固定叫 `installer.zip`，
改名前先在线上 `curl -I` 试一次。

#### 客户端是「桌面窗口」，不是浏览器页面

`tools/desktop_app.py` 用 pywebview + WebView2 开一个独立窗口：没有地址栏、没有标签页，
任务栏里就是「智鲸 AI」，标题栏是白底圆角图标，关窗即退出、后台 uvicorn 一起结束。

- 依赖：`backend/requirements-desktop.txt`（`pywebview`），由 `setup.ps1` 装，**装不上不影响使用**
- 降级链：WebView2 窗口 → Edge `--app` 模式（同样无地址栏）→ 默认浏览器
- 少一次往返：`desktop_app.py` 自己拉起 uvicorn 子进程并做健康检查，页面就绪才建窗口，
  不会出现「窗口先开、白屏几秒」
- 调试：`python tools/desktop_app.py --debug`（WebView 里可右键检查元素）；
  只想起服务：`--no-window`

★ **重装会保留 `backend/.env`**：安装器先把它读进内存，解压完再写回。
里面是管理员密码、JWT 密钥与模型配置，丢了用户就得重新记一遍新密码。

#### 客户端首次启动

客户端包里没有 `.env`，直接启动会在导入期抛 `ValidationError`。给了两条路：

```bash
# 推荐：装了 Ollama 就零配置，用本地模型
python tools/ollama_bootstrap.py --write-env     # 会自动补管理员账号与 JWT 密钥
# 或者：改用云端
#   backend/.env 写 MODEL_BACKEND=zhipu + ZHIPU_API_KEY=...
```

打包时会自动给 `.ps1` 补 **UTF-8 BOM**。这一步不能省：PowerShell 5.1 遇到无 BOM 的
UTF-8 会按系统 ANSI（简体中文下是 GBK）解码，脚本里所有中文提示都会变成乱码。

---

## 七、验证清单

交付前建议按这个顺序过一遍：

- [ ] 把 `index.html` 单独复制到空目录，断网双击打开 → 页面完整、logo 正常
- [ ] 浏览器开发者工具 Network 面板 → 外部请求为 0（只有 `file://` 或 `127.0.0.1`）
- [ ] 分别用 1440 / 834 / 390 宽度看一遍 → 无横向滚动条
- [ ] 移动端汉堡菜单 → 能开、点链接后自动收起
- [ ] FAQ 点开一条，再点另一条 → 前一条自动收起
- [ ] 安装后桌面出现快捷方式，双击能打开页面
- [ ] `start.cmd -Server` → 浏览器停在 `http://127.0.0.1:8080/`，页面正常
- [ ] `uninstall.cmd` → 目录、快捷方式、注册项都清掉了

自动化脚本（需要 Playwright）：

```bash
node frontend/scripts/verify-landing.mjs            # 三档视口的渲染与响应式
node frontend/scripts/verify-landing-standalone.mjs # 隔离目录 + 零外部请求
```
