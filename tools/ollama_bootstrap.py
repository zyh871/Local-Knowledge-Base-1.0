#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智鲸 AI · 本地模型自检与接入（Ollama）

这个程序回答一个问题：**这台机器能不能用本地模型对话？**
能，就直接接上去聊天；不能，就说清楚差哪一步、补哪一步。

只依赖 Python 标准库（3.8+），不需要 requests、不需要装 Ollama 的 Python SDK，
也不会在没装 Ollama 的机器上报错退出——那种情况本身就是它要报告的结果。

检测分三段，逐段排除：

  1. 安装态  —— 磁盘上有没有 ollama 可执行文件（PATH + 各平台常见安装路径）
  2. 服务态  —— 11434 端口上的 HTTP 服务能不能应答（GET /api/version）
  3. 模型态  —— /api/tags 里有没有**可对话**的模型（排除纯 embedding 模型）

  为什么要分开判：三者可以互相独立。比如 ollama 装了但服务没起（Windows 上
  关闭了开机自启）、服务在跑但一个模型都没拉、或者干脆是公司的远程 Ollama
  服务（本机没装二进制也能用）。只看「有没有命令」会误判。

用法：
    python tools/ollama_bootstrap.py                 # 自检 + 报告
    python tools/ollama_bootstrap.py --chat          # 自检通过后进对话
    python tools/ollama_bootstrap.py --once "你好"    # 单轮问答
    python tools/ollama_bootstrap.py --json          # 机器可读结果
    python tools/ollama_bootstrap.py --write-env     # 生成 backend/.env（本地模型）
    python tools/ollama_bootstrap.py --serve         # 用本地模型启动智鲸 AI

退出码：0 可用 / 3 未安装 / 4 有服务但无可对话模型 / 5 装了但服务没起
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")

# 判定「这个模型不能聊天」的关键词。Ollama 的 /api/tags 不区分用途，
# 这些是常见的纯向量模型；把它们当对话模型选会得到一个 400 或空回复。
_EMBED_HINTS = (
    "embed", "bge", "mxbai", "minilm", "gte", "e5-", "jina-embed", "paraphrase",
)

# 中文场景的偏好排序。命中越靠前越优先——同等条件下先选中文强的。
_CHAT_PREFERENCE = (
    ("qwen", 100), ("glm", 90), ("deepseek", 85), ("yi", 80),
    ("llama3", 60), ("llama", 55), ("gemma", 50), ("mistral", 45),
    ("phi", 30), ("tinyllama", 10),
)

# 参数规模 → 大致显存/内存占用（GB，Q4 量化）。用于判断这台机器扛不扛得住。
_SIZE_GB = {
    "0.5b": 0.6, "1b": 1.0, "1.5b": 1.4, "2b": 1.8, "3b": 2.6, "4b": 3.2,
    "7b": 5.2, "8b": 5.8, "9b": 6.5, "13b": 9.5, "14b": 10.0, "27b": 18.0,
    "32b": 21.0, "70b": 45.0,
}


# ---------------------------------------------------------------------------
#  基础探测
# ---------------------------------------------------------------------------
def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """绕过 HTTP_PROXY/HTTPS_PROXY。

    ★ 本机实测过：环境里配了一个不可达的代理时，urllib 默认会走 trust_env，
    对 127.0.0.1 的请求也会被送进代理，结果是「Ollama 明明在跑却连不上」。
    本地回环请求绝不能经过代理。
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


_OPENER = _no_proxy_opener()


def http_json(url: str, payload: dict | None = None, timeout: float = 3.0):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with _OPENER.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def find_binary() -> str | None:
    """在 PATH 与各平台默认安装位置里找 ollama 可执行文件。"""
    hit = shutil.which("ollama")
    if hit:
        return hit

    system = platform.system()
    candidates: list[Path] = []
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        if local:
            candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
        candidates.append(Path(pf) / "Ollama" / "ollama.exe")
        candidates.append(Path(r"C:\Program Files (x86)\Ollama\ollama.exe"))
    elif system == "Darwin":
        candidates += [
            Path("/usr/local/bin/ollama"),
            Path("/opt/homebrew/bin/ollama"),
            Path("/Applications/Ollama.app/Contents/Resources/ollama"),
        ]
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama"), Path("/snap/bin/ollama")]

    for p in candidates:
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    return None


def total_ram_gb() -> float | None:
    """总内存。不引第三方库：Windows 走 ctypes，其它系统读内核接口。"""
    try:
        if platform.system() == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return round(st.ullTotalPhys / 1024**3, 1)
        elif platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
            return round(int(out.stdout.strip()) / 1024**3, 1)
        else:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / 1024**2, 1)
    except Exception:
        return None
    return None


def parse_size_gb(name: str, raw_size: int | None) -> float | None:
    """模型体量：优先用 /api/tags 报的字节数，退化时按参数规模估算。"""
    if raw_size:
        return round(raw_size / 1024**3, 1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", name.lower())
    if m:
        return _SIZE_GB.get(f"{m.group(1)}b")
    return None


# ---------------------------------------------------------------------------
#  检测结果
# ---------------------------------------------------------------------------
@dataclass
class Model:
    name: str
    size_gb: float | None
    is_chat: bool
    score: int = 0


@dataclass
class Report:
    state: str = "NOT_INSTALLED"          # READY / NO_MODEL / SERVICE_DOWN / NOT_INSTALLED
    base_url: str = DEFAULT_BASE
    binary: str | None = None
    version: str | None = None
    service_ok: bool = False
    models: list[Model] = field(default_factory=list)
    chosen: str | None = None
    ram_gb: float | None = None
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def chat_models(self) -> list[Model]:
        return [m for m in self.models if m.is_chat]

    @property
    def embed_models(self) -> list[Model]:
        return [m for m in self.models if not m.is_chat]


def _score(name: str) -> int:
    low = name.lower()
    best = 0
    for key, val in _CHAT_PREFERENCE:
        if key in low:
            best = max(best, val)
    return best


def detect(base_url: str = DEFAULT_BASE, retries: int = 1) -> Report:
    t0 = time.time()
    rep = Report(base_url=base_url)
    rep.binary = find_binary()
    rep.ram_gb = total_ram_gb()

    # ── 服务态 ──────────────────────────────────────────────
    last_err = None
    for attempt in range(retries + 1):
        try:
            ver = http_json(f"{base_url}/api/version", timeout=2.5)
            rep.service_ok = True
            rep.version = ver.get("version")
            break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(0.4)

    if not rep.service_ok:
        rep.error = str(last_err) if last_err else "无法连接"
        # 装了软件但服务没起 → SERVICE_DOWN；连二进制都没有 → NOT_INSTALLED
        rep.state = "SERVICE_DOWN" if rep.binary else "NOT_INSTALLED"
        rep.elapsed_ms = int((time.time() - t0) * 1000)
        return rep

    # ── 模型态 ──────────────────────────────────────────────
    try:
        tags = http_json(f"{base_url}/api/tags", timeout=5.0)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        rep.error = f"/api/tags 读取失败：{exc}"
        rep.state = "NO_MODEL"
        rep.elapsed_ms = int((time.time() - t0) * 1000)
        return rep

    for item in tags.get("models") or []:
        name = item.get("name") or item.get("model") or ""
        if not name:
            continue
        low = name.lower()
        is_chat = not any(h in low for h in _EMBED_HINTS)
        rep.models.append(
            Model(name=name, size_gb=parse_size_gb(name, item.get("size")), is_chat=is_chat, score=_score(name))
        )

    # 排序：中文强的优先，同档选体积小的（更可能在内存里跑起来）
    rep.models.sort(key=lambda m: (-m.score, m.size_gb or 99))
    if rep.chat_models:
        rep.state = "READY"
        rep.chosen = rep.chat_models[0].name
    else:
        rep.state = "NO_MODEL"

    rep.elapsed_ms = int((time.time() - t0) * 1000)
    return rep


# ---------------------------------------------------------------------------
#  人读报告 + 引导
# ---------------------------------------------------------------------------
_ICON = {"READY": "[OK]", "NO_MODEL": "[!]", "SERVICE_DOWN": "[!]", "NOT_INSTALLED": "[X]"}

_OLLAMA_URL = "https://ollama.com/download"


def print_report(rep: Report) -> None:
    line = "─" * 62
    print()
    print("  智鲸 AI · 本地模型自检")
    print(f"  {line}")
    print(f"  平台          {platform.system()} {platform.release()}")
    if rep.ram_gb:
        print(f"  内存          {rep.ram_gb} GB")
    print(f"  Ollama 程序   {rep.binary or '未找到'}")
    print(f"  服务地址      {rep.base_url}")
    print(
        "  服务状态      "
        + (f"运行中（v{rep.version}）" if rep.service_ok else f"无法连接（{rep.error}）")
    )

    if rep.models:
        print(f"  本地模型      {len(rep.models)} 个")
        for m in rep.models:
            kind = "对话" if m.is_chat else "向量"
            size = f"{m.size_gb} GB" if m.size_gb else "体积未知"
            mark = " ← 将使用" if m.name == rep.chosen else ""
            print(f"                · {m.name:<28} {kind}  {size}{mark}")
    else:
        print("  本地模型      无")

    print(f"  {line}")
    print(f"  {_ICON[rep.state]} 结论：{_STATE_TEXT[rep.state]}")
    print()

    if rep.state != "READY":
        print_guidance(rep)


_STATE_TEXT = {
    "READY": "检测到可用的本地模型，可以直接对话（数据不离开本机）",
    "NO_MODEL": "Ollama 服务在跑，但一个可对话的模型都没有",
    "SERVICE_DOWN": "装了 Ollama，但后台服务没有启动",
    "NOT_INSTALLED": "这台机器上没检测到 Ollama",
}


def print_guidance(rep: Report) -> None:
    system = platform.system()

    if rep.state == "NOT_INSTALLED":
        print("  有两条路，任选其一：")
        print()
        print("  ── 路线 A：用本地模型（免费、离线、数据不出本机）")
        if system == "Windows":
            print(f"     1. 下载安装：{_OLLAMA_URL}")
            print("        （下完是个 OllamaSetup.exe，双击安装，无需管理员）")
        elif system == "Darwin":
            print(f"     1. 下载安装：{_OLLAMA_URL}   或    brew install ollama")
        else:
            print("     1. 安装： curl -fsSL https://ollama.com/install.sh | sh")
        print(f"     2. 拉一个模型：{_pull_hint(rep.ram_gb)}")
        print("     3. 重新运行本程序：python tools/ollama_bootstrap.py")
        print()
        print("  ── 路线 B：用云端模型的 API（不用装任何东西，需要密钥）")
        print("     在 backend/.env 里填：")
        print("       MODEL_BACKEND=zhipu")
        print("       ZHIPU_API_KEY=你的密钥        # https://open.bigmodel.cn")
        print("     也可以用 OpenAI 兼容的任意端点：")
        print("       MODEL_BACKEND=openai")
        print("       OPENAI_API_KEY=...  OPENAI_BASE_URL=...")
        print()

    elif rep.state == "SERVICE_DOWN":
        print("  Ollama 装了但服务没起来。试试：")
        print()
        if system == "Windows":
            print(f'     "{rep.binary}" serve')
            print("     或从开始菜单启动 Ollama（装了之后它默认开机自启）")
        elif system == "Darwin":
            print("     open -a Ollama")
        else:
            print("     ollama serve &")
        print()
        print("  起来后重新运行本程序。若端口不是 11434，用 --base-url 指定。")
        print()

    elif rep.state == "NO_MODEL":
        print("  服务是通的，只是没有模型。拉一个：")
        print()
        print(f"     {_pull_hint(rep.ram_gb)}")
        print()
        if rep.embed_models:
            print("  注意：检测到的都是向量模型（" + "、".join(m.name for m in rep.embed_models) + "），")
            print("        它们只用于文档检索，不能对话。")
            print()

    print("  说明：本程序不会替你下载模型（几个 GB 的流量应该由你决定）。")
    print()


def _pull_hint(ram_gb: float | None) -> str:
    """按内存推荐一个模型——太大跑不动，太小答不好。

    阈值按「可用内存」而不是标称容量取：16 GB 的机器实际报 15.x GB，
    7B 的 Q4 量化约 4.7 GB，跑得动；8 GB 的机器就只建议 3B。
    """
    if not ram_gb or ram_gb >= 12:
        return "ollama pull qwen2.5:7b        # 中文效果好，约 4.7 GB"
    if ram_gb >= 7:
        return "ollama pull qwen2.5:3b        # 约 2 GB，8 GB 内存够用"
    return "ollama pull qwen2.5:1.5b      # 约 1 GB，内存紧张时用"


# ---------------------------------------------------------------------------
#  对话接入
# ---------------------------------------------------------------------------
def chat_stream(
    rep: Report,
    model: str,
    messages: list[dict],
    system: str | None = None,
    temperature: float = 0.7,
    show_thinking: bool = False,
) -> str:
    """流式对话。用 Ollama 原生 /api/chat（NDJSON），比 OpenAI 兼容层稳。

    yield 式的分片里，思考型模型（qwen3、deepseek-r1）会把推理过程放在
    message.thinking 里，正文在 message.content 里，两者要分开取——
    混在一起会把思维链当成回答显示给用户。
    """
    payload_msgs = list(messages)
    if system:
        payload_msgs = [{"role": "system", "content": system}] + payload_msgs

    body = {
        "model": model,
        "messages": payload_msgs,
        "stream": True,
        "options": {"temperature": temperature},
    }
    req = urllib.request.Request(
        f"{rep.base_url}/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )

    answer: list[str] = []
    with _OPENER.open(req, timeout=300) as resp:
        for raw in resp:
            raw = raw.strip()
            if not raw:
                continue
            try:
                chunk = json.loads(raw.decode("utf-8"))
            except ValueError:
                continue
            msg = chunk.get("message") or {}
            if show_thinking and msg.get("thinking"):
                print(msg["thinking"], end="", flush=True)
            piece = msg.get("content") or ""
            if piece:
                answer.append(piece)
                print(piece, end="", flush=True)
            if chunk.get("done"):
                break
    print()
    return "".join(answer)


def run_chat(rep: Report, args) -> int:
    model = args.model or rep.chosen
    if not model:
        print("  没有可用的对话模型，先处理上面的提示。", file=sys.stderr)
        return 4

    print(f"  正在使用本地模型：{model}")
    print(f"  {rep.base_url}    （输入 /exit 退出，/clear 清空上下文）")
    print()

    history: list[dict] = []
    while True:
        try:
            text = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("/exit", "/quit", "exit", "quit"):
            break
        if text == "/clear":
            history.clear()
            print("  （上下文已清空）")
            continue

        history.append({"role": "user", "content": text})
        print("智鲸 > ", end="", flush=True)
        try:
            answer = chat_stream(rep, model, history, args.system, show_thinking=args.thinking)
        except urllib.error.URLError as exc:
            print(f"\n  请求失败：{exc}", file=sys.stderr)
            history.pop()
            continue
        history.append({"role": "assistant", "content": answer})
        print()
    return 0


# ---------------------------------------------------------------------------
#  写 .env / 启动智鲸 AI
# ---------------------------------------------------------------------------
def write_env(rep: Report, env_path: Path, model: str) -> None:
    """把「用本地模型」这一组配置写进 backend/.env。

    只改需要的键，其它内容原样保留——用户的密钥、端口、自定义配置不能丢。

    顺带补两个**首次启动的硬性前提**：`SecuritySettings` 在校验期就要求
    ADMIN_USERNAME / ADMIN_PASSWORD 非空，缺了会直接抛 ValidationError
    （用户看到的是一屏 traceback）。所以这里给一个随机初始密码并打印出来，
    JWT_SECRET_KEY 也一并生成，避免所有人共用一个默认密钥。
    """
    keys = {
        "MODEL_BACKEND": "ollama",
        "OLLAMA_BASE_URL": rep.base_url,
        "LLM_MODEL": model,
    }
    # 有本地向量模型就一并指定；没有就用零依赖的 TF-IDF。
    # ★ 不能留空：默认的 nomic-embed-text 多数机器上没拉过，而 Chroma 在
    #   嵌入不可用时会回落到内置 ONNX 模型（首次要联网下 79 MB，还会卡死事件循环），
    #   表现就是「上传文档一直转圈」。
    embed = rep.embed_models[0].name if rep.embed_models else "local-tfidf"
    keys["EMBEDDING_MODEL"] = embed

    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    present = {m.group(1) for m in (re.match(r"\s*([A-Z0-9_]+)\s*=", l) for l in lines) if m}

    new_password = None
    if "ADMIN_USERNAME" not in present:
        keys["ADMIN_USERNAME"] = "admin"
    if "ADMIN_PASSWORD" not in present:
        new_password = secrets.token_urlsafe(12)
        keys["ADMIN_PASSWORD"] = new_password
    if "JWT_SECRET_KEY" not in present:
        keys["JWT_SECRET_KEY"] = secrets.token_hex(32)

    if env_path.exists():
        backup = env_path.with_suffix(env_path.suffix + ".bak")
        backup.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  已备份原配置 -> {backup}")

    remaining = dict(keys)
    out: list[str] = []
    for line in lines:
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in remaining:
            k = m.group(1)
            out.append(f"{k}={remaining.pop(k)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# 本地模型（由 tools/ollama_bootstrap.py 写入）")
        for k, v in remaining.items():
            out.append(f"{k}={v}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  已写入 {env_path}")
    for k in ("MODEL_BACKEND", "OLLAMA_BASE_URL", "LLM_MODEL", "EMBEDDING_MODEL"):
        if k in keys:
            print(f"    {k}={keys[k]}")
    if new_password:
        print()
        print(f"  已生成管理员账号：admin / {new_password}")
        print("  这个密码只显示这一次，请记下来（也可以之后在 .env 里改 ADMIN_PASSWORD）。")


def find_python(root: Path) -> str | None:
    for rel in ("backend/venv/Scripts/python.exe", "backend/.venv/Scripts/python.exe",
                "backend/venv/bin/python", "backend/.venv/bin/python"):
        p = root / rel
        if p.is_file():
            return str(p)
    return sys.executable


def run_serve(rep: Report, root: Path, model: str, port: int) -> int:
    """用本地模型把智鲸 AI 跑起来。

    通过**环境变量**覆盖，不改 .env：pydantic-settings 里环境变量优先级高于
    dotenv，所以这里设的 MODEL_BACKEND 一定生效，且退出后不留下改装。
    """
    python = find_python(root)
    backend = root / "backend"
    if not backend.is_dir():
        print(f"  找不到后端目录：{backend}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env.update({
        "MODEL_BACKEND": "ollama",
        "OLLAMA_BASE_URL": rep.base_url,
        "LLM_MODEL": model,
        "SERVE_FRONTEND_DIR": str(root / "frontend" / "dist"),
        "PYTHONUTF8": "1",
    })
    if rep.embed_models:
        env["EMBEDDING_MODEL"] = rep.embed_models[0].name

    print(f"  启动：{python} -m uvicorn app.main:app --port {port}")
    print(f"  模型：{model}（{rep.base_url}）")
    print()
    try:
        return subprocess.call(
            [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(backend),
            env=env,
        )
    except KeyboardInterrupt:
        return 0


# ---------------------------------------------------------------------------
#  main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve()
    root = here.parents[1] if here.parent.name == "tools" else Path.cwd()

    ap = argparse.ArgumentParser(
        description="检测本机 Ollama 与本地模型，并接入对话",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--base-url", default=DEFAULT_BASE, help=f"Ollama 服务地址（默认 {DEFAULT_BASE}）")
    ap.add_argument("--model", help="指定模型，默认自动挑选")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--quiet", action="store_true", help="只输出结论（配合退出码使用）")
    ap.add_argument("--chat", action="store_true", help="自检通过后进入多轮对话")
    ap.add_argument("--once", metavar="QUESTION", help="单轮提问后退出")
    ap.add_argument("--system", help="系统提示词")
    ap.add_argument("--thinking", action="store_true", help="显示思考型模型的推理过程")
    ap.add_argument("--write-env", nargs="?", const="", metavar="PATH",
                    help="把本地模型配置写入 .env（默认 backend/.env）")
    ap.add_argument("--serve", action="store_true", help="用本地模型启动智鲸 AI")
    ap.add_argument("--port", type=int, default=8000, help="--serve 的端口（默认 8000）")
    args = ap.parse_args(argv)

    rep = detect(args.base_url)

    if args.json:
        print(json.dumps({
            "state": rep.state,
            "base_url": rep.base_url,
            "binary": rep.binary,
            "version": rep.version,
            "service_ok": rep.service_ok,
            "chosen": rep.chosen,
            "models": [{"name": m.name, "size_gb": m.size_gb, "chat": m.is_chat} for m in rep.models],
            "ram_gb": rep.ram_gb,
            "error": rep.error,
            "elapsed_ms": rep.elapsed_ms,
        }, ensure_ascii=False, indent=2))
    elif args.quiet:
        print(rep.state)
    else:
        print_report(rep)

    if rep.state != "READY":
        # 没检测到就是最终结论，按状态给出不同退出码
        return {"NOT_INSTALLED": 3, "NO_MODEL": 4, "SERVICE_DOWN": 5}.get(rep.state, 1)

    model = args.model or rep.chosen

    if args.write_env is not None:
        target = Path(args.write_env) if args.write_env else (root / "backend" / ".env")
        write_env(rep, target, model)

    if args.serve:
        return run_serve(rep, root, model, args.port)

    if args.once:
        # chat_stream 自己负责边收边打印，这里不要再写一次返回值
        print(f"  [{model}] ", end="")
        chat_stream(rep, model, [{"role": "user", "content": args.once}],
                    args.system, show_thinking=args.thinking)
        return 0

    if args.chat:
        return run_chat(rep, args)

    if not (args.json or args.quiet):
        print("  下一步：")
        print("    python tools/ollama_bootstrap.py --chat        直接对话")
        print("    python tools/ollama_bootstrap.py --write-env   让智鲸 AI 用本地模型")
        print("    python tools/ollama_bootstrap.py --serve       用本地模型启动智鲸 AI")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
