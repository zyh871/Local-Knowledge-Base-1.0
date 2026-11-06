# 依赖集成进安装包：可行性评估

> 结论先说：**可以做到，但不该把「离线依赖」塞进主安装包。**
> 推荐「主包保持小 + 另发一个可选的离线完整包」。

本文的所有数字都来自本仓库当前状态（Windows / Python 3.13 / x64），不是估算模型。

---

## 一、现状：用户实际要付出什么

| 阶段 | 下载量 | 说明 |
|---|---|---|
| 客户端安装器 | **12 MB** | `智鲸AI客户端-1.0.0-安装.exe` |
| `setup.cmd` 装依赖 | **400–500 MB** | pip 从 PyPI 拉 wheel |
| 合计 | **≈ 450–510 MB** | 其中绝大部分是依赖，不是程序本身 |

装完之后本机占用的空间：

```
backend\venv                590 MB   （已装 162 个包，含 43 个直接依赖）
├─ kubernetes                83 MB   ← 传递依赖，体积最大的一块
├─ chromadb_rust_bindings    61 MB
├─ onnxruntime               45 MB   ← Chroma 默认嵌入模型的运行时
├─ numpy                     34 MB
├─ numpy.libs                21 MB
├─ langchain_community       23 MB
└─ ……
```

也就是说：**用户下载 12 MB 的东西，最后为了让它跑起来要再下载 400–500 MB。**

---

## 二、方案对比

### 方案 A：把 wheelhouse 塞进主安装包（离线安装）

做法：

```bash
pip download -r requirements.txt -r requirements-desktop.txt \
    -d wheels/ --only-binary=:all: --platform win_amd64
# 安装时
pip install --no-index --find-links=wheels -r requirements.txt
```

| 维度 | 影响 |
|---|---|
| 安装包体积 | 12 MB → **约 300 MB**（wheel 本身已压缩，zip 后基本不变） |
| 用户总下载量 | 450–510 MB → **约 300 MB**（少 30%–40%，因为不用重复下载） |
| 安装耗时 | 联网解析依赖 3–10 分钟 → 离线解包安装 **1–3 分钟**（更稳，不受网速与源可用性影响） |
| 断网/内网 | ✅ 彻底可用 |
| 依赖更新 | ⚠️ **每次改 requirements 都要重出 300 MB 的包**；发布频率会被大包拖住 |
| 跨平台 | ⚠️ wheel 与平台绑定：win_amd64 / macosx_arm64 各要一份，包体翻倍 |
| 杀软误报 | ⚠️ 大包 + 大量 exe/dll 更容易被启发式拦截（当前 12 MB 的包已经偶发被拦） |

### 方案 B：PyInstaller 打包后端（onefile）

| 维度 | 影响 |
|---|---|
| 体积 | 120–180 MB（含解释器与依赖） |
| 优点 | 免安装、免 Python、启动即用 |
| 缺点 | 启动慢（解压 100 MB）、杀软误报率高、**动态依赖容易翻车**——本项目有 `chromadb_rust_bindings`、`onnxruntime`、`numpy.libs`、Tesseract OCR、PIL，这些在 onefile 下都可能出问题 |
| 结论 | 不推荐作为主路径（客户端已经用「装依赖」的方式跑通了） |

### 方案 C：主包保持小 + 另发离线完整包（**推荐**）

```
主包    智鲸AI客户端-1.0.0-安装.exe            12 MB   ← 在线装依赖，默认下载
可选    zhijing-ai-client-1.0.0-offline-win-x64.zip   ~300 MB  ← 内含 wheels/，双击即装
```

| 维度 | 影响 |
|---|---|
| 默认体验 | 不变：12 MB 下载，pip 走 CDN，实际速度通常比自己下 wheelhouse 更快 |
| 离线用户 | 按需下载 300 MB 的完整包，**不再需要联网**，也不受 PyPI 可达性影响 |
| 依赖更新 | 只有需要离线包时才重出大包；主包照常小步快跑 |
| 发布成本 | 多一条流水线，但可以只在 LTS 版本出离线包 |
| 体积代价 | 用户磁盘上多一份 wheels（装完可删） |

---

## 三、建议

**保留在线安装作为默认，并额外提供离线完整包**，理由：

1. **总量并不增加**。在线方案本来就要下 400–500 MB，把 wheel 打包并没有让用户少下多少，
   但把「发布一次要重出 300 MB」的负担留给了自己。
2. **在线装依赖更抗变化**。pip 会自己解决版本兼容；离线包一旦与 `requirements.lock.txt`
   漂移，就会出现「本地装得上、换台机器装不上」的诡异问题。
3. **离线包解决的是「真没有网」的人**（内网、隔离网、公司网关），这是一个真实但小众的群体，
   不值得让 95% 的用户都下载 25 倍大的包。
4. **杀软误报**在小包上已经出现过（IExpress 单文件 exe 被拦过），300 MB 的包风险更高。

### 如果要做离线包，注意三件事

1. **必须用 `--only-binary=:all:`**：源码包在目标机器上要编译，Windows 上没有编译器。
2. **必须固定 Python 版本与平台**：`--platform win_amd64 --python-version 3.13`，
   否则下到的是当前机器的 cp313 wheel，换机器就装不上。
3. **`requirements-desktop.txt` 也要一起下**：pywebview 及其 pythonnet 依赖
   在 pythonnet 上没有 Windows 以外平台的 wheel，需要按平台分别准备。

### 还有一条几乎零成本的路

很多用户其实已经有 pip 缓存。在联网机器上先跑一次

```bash
pip download -r requirements.txt -d %LOCALAPPDATA%\pip-cache
```

把缓存目录拷过去，安装时用 `--find-links` 指过去即可 —— 不用发大包，
也不用改我们的安装流程。

---

## 四、当前实现位置

| 文件 | 作用 |
|---|---|
| `landing/deploy/build_client.py` | 生成客户端安装包（`assemble()` 决定打哪些文件） |
| `landing/deploy/installer_gui.py` | 图形安装器（`ensure_env()` 写 `.env`） |
| `backend/requirements.txt` | 43 个直接依赖 |
| `backend/requirements-desktop.txt` | 桌面窗口组件（pywebview），可选 |
| `backend/requirements.lock.txt` | 锁定版本，离线包必须用它 |
