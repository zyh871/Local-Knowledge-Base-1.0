# 安装包内置配置：实现说明

> 目标：用户双击安装包 → 填个目录 → 装完就能用。
> 不需要改 `.env`、不需要手动跑 `setup.cmd`、不需要去界面里逐个填密钥。

---

## 一、配置文件怎么进安装包

```
landing/deploy/
├── default_config.json     ← 默认档案（构建时被复制成包内 config/profile.json）
└── build_client.py         ← assemble() 负责把 config/ 写进包体
```

`build_client.py` 打包时会在包体里生成两个文件：

```
<安装包根目录>/
└── config/
    ├── profile.json   ← 配置档案本体（env / search / endpoints）
    └── source.json    ← 可选的远端地址 + sha256
```

构建方式：

```bash
# 用默认档案
python landing/deploy/build_client.py

# 用自己准备的档案
python landing/deploy/build_client.py --profile D:\deploy\acme-profile.json

# 指定远端下发地址（安装时会先尝试下载，失败自动回退到包内）
python landing/deploy/build_client.py --config-url https://cfg.example.com/zhijing/profile.json \
                                      --config-sha256 9f2c...（可选）
```

`profile.json` 的结构（schema 版本 1）：

```json
{
  "schema": 1,
  "name": "某客户 · 统一云端配置",
  "env": {
    "MODEL_BACKEND": "zhipu",
    "LLM_MODEL": "glm-4-flash",
    "EMBEDDING_MODEL": "embedding-3",
    "ZHIPU_API_KEY": "…",
    "ALLOW_CUSTOM_MODELS": "true"
  },
  "search": { "provider": "tavily", "api_key": "tvly-…" },
  "endpoints": [
    {
      "name": "Gemini · Gemini 2.5 Flash",
      "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
      "api_key": "…",
      "model": "gemini-2.5-flash",
      "enabled": true
    }
  ]
}
```

三块各管一段，且都遵守「**只补缺，不覆盖**」：

| 字段 | 落到哪 | 写入条件 |
|---|---|---|
| `env` | `backend/.env` | 键不存在时追加；已存在则跳过（升级不覆盖用户配置） |
| `search` | `backend/data/search.db` | 本地没有 `provider` 记录时才写 |
| `endpoints` | `backend/data/custom_models.db` | `model_endpoints` 表为空时才写 |

`ADMIN_USERNAME` / `ADMIN_PASSWORD` / `JWT_SECRET_KEY` 若档案没给，安装器随机生成；
`MODEL_BACKEND` 若档案没给，才去做本机 Ollama 探测——**运维的意图优先于机器探测**。

---

## 二、安装时怎么触发「下载 → 校验 → 写入」

`install()` 在解压完成之后按固定顺序执行：

```
解压 → 恢复旧 .env → 建快捷方式 → 写卸载入口
   → resolve_profile()   取配置（远端 → 包内 → 内置）
   → apply_profile()     写 .env / search.db / custom_models.db
   → run_deps()          自动建 venv + 装依赖
   → write_report()      写 config/applied.json
```

### 2.1 取配置：三级回退

```
① 远端   config/source.json 里的 config_url（或构建时 --config-url，或安装时 --config-url）
   ├─ 12 秒超时，最多 3 次（退避 0.6s / 1.2s）
   ├─ 可选 sha256 校验
   └─ 超过 512 KB 直接判异常
② 包内   config/profile.json
③ 内置   BUILTIN_PROFILE（空档案，全部走本机探测）
```

每一次尝试都会记进 `applied.json` 的 `profile.attempts`，出问题时能直接看出
「远端挂在哪一步、回退到了哪一级」。

### 2.2 写配置

- **`.env`**：`merge_env_file()` 逐行解析，命中的键就地替换，没有的追加，注释与顺序保持原样。
- **`search.db`**：直接用 sqlite3 建表 + upsert（表结构与 `search_repo.py` 一致）。
- **`custom_models.db`**：同上，表结构与 `model_repo.py` 一致。

写入 SQLite 而不是「让用户进界面配一遍」，是因为这两张表本来就是应用自己的存储，
安装器只是充当第一个写入者——后端启动后读到的就是同一份数据。

### 2.3 自动装依赖

`run_deps()` 调 `setup.ps1`（`-NoProfile -ExecutionPolicy Bypass`），
把 stdout 逐行写进安装日志，同时把进度条推到 86%–98%。

已经存在 `backend/.venv/Scripts/python.exe` 时直接跳过——重复执行安装包不会重复装。

GUI 上有一个默认勾选的「同时安装运行环境」：
勾着就一步到位；取消则只装文件，之后双击 `setup.cmd` 或点「重试安装环境」补。

---

## 三、异常与失败处理

设计原则：**配置是加分项，不能成为装不上的理由。**

| 异常 | 处理 |
|---|---|
| 远端连不上 / 超时 / 5xx | 重试 3 次后放弃，回退包内档案；`attempts` 记录真实异常（如 `WinError 10061`） |
| 远端返回非 JSON | 解析失败即判该级不可用，回退下一级 |
| sha256 不匹配 | 直接判该级不可用（不写任何东西），回退下一级 |
| schema 版本过高 | `_valid_profile` 拒绝（`schema=99 高于本安装器支持的 1`），回退下一级 |
| `env` / `endpoints` 类型不对 | 同上，整份丢弃，不写半份坏配置 |
| `endpoints` 条目缺 `model`/`base_url` | 同上 |
| 包内没有 `config/profile.json` | 回退内置档案（完全靠本机探测），不影响安装 |
| `.env` 写入失败（权限/占用） | 安装照常完成，`applied.json` 里 `env_keys` 为空，界面提示日志路径 |
| 依赖安装失败 | 安装**仍算成功**：文件已就位，标记 `dependencies.ok=false` 并给出重试按钮与手动命令 |
| 磁盘空间不足 | `check_target()` 在选目录时先写探测文件；依赖安装失败时会给出 pip 原始报错 |
| 升级重装 | `.env` 先备份后恢复；`env_keys` 为空 = 一个键都没被改 |

失败时用户看到的不是一屏 traceback，而是：

```
⚠ 运行环境没装成功（多半是网络或 pip 源的问题）。
   文件已经装好，点下面的「重试安装环境」即可，不必重装。
```

---

## 四、安装后怎么核对

安装目录里会多一个 `config/applied.json`：

```json
{
  "installed_at": "2026-11-04 08:42:07",
  "installer_version": "1.0.0",
  "target": "C:\\ZJ-CFG2",
  "profile": {
    "source": "remote",
    "detail": "远端配置（某客户 · 统一云端配置）",
    "attempts": [
      { "source": "remote", "url": "http://…/profile.json", "ok": true, "detail": "…" }
    ]
  },
  "applied": {
    "env_keys": ["MODEL_BACKEND", "ZHIPU_API_KEY", "…”],
    "search": "已写入搜索配置：tavily",
    "endpoints": "已预置 2 个模型端点",
    "note": ""
  },
  "dependencies": { "ok": false, "skipped": true, "tail": "安装时未选择安装运行环境" }
}
```

日志在 `%TEMP%\zhijing-client-install.log`，每次安装都会追加一段。

无界面部署（批量）用：

```bat
智鲸AI客户端-1.0.0-安装.exe --silent --dir "D:\智鲸AI"
智鲸AI客户端-1.0.0-安装.exe --silent --dir "D:\智鲸AI" --no-deps
智鲸AI客户端-1.0.0-安装.exe --silent --dir "D:\智鲸AI" --config-url https://…/profile.json
智鲸AI客户端-1.0.0-安装.exe --silent --dir "D:\智鲸AI" --force-config
```

`--force-config` 会覆盖已有配置（包括重新生成管理员密码），只在需要重置时用。

---

## 五、已知边界

1. **档案里明文存密钥**。`.env` 本来就是明文，包内档案同级别；
   但如果配置文件要外发，建议只放「模型端点 + 指向密钥服务的地址」。
2. **远端配置没有签名，只有 sha256**。sha256 只能防传输损坏、不能防篡改——
   要防篡改需要把公钥打包进安装器做签名校验（当前未实现）。
3. **只写 `.env` 与两个 SQLite 表**。不碰工作流、会话、文档等用户数据。
4. **依赖安装是「尽力而为」**：网络受限时可能失败，已按上面的方式降级。
