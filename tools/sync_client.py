"""把仓库里的最新代码同步到已安装的客户端，并补齐创作相关配置。

为什么要有这个脚本
    项目是「源码部署」的客户端：装好的目录里就是一份 app/ + dist/。
    每次改完都要手工复制一次很蠢，而且容易漏（漏了表现是「改了没生效」）。
    这里做三件事，顺序固定：
      1. 备份 .env（万一要回滚）
      2. 同步 backend/app、tools、frontend/dist
      3. 按「只补缺」的规则写创作相关配置

不碰：data/（用户文档、会话、库）、chroma_db/、backend/.venv
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

REPO = Path(r"C:\Users\a1731\Desktop\Local-Knowledge-Base-1.0-main")
CLIENT = Path(r"C:\Users\a1731\Desktop\下载文件\ZhijingAIClient")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def sync_tree(src: Path, dst: Path) -> int:
    """把 src 覆盖到 dst；跳过缓存文件。返回复制的文件数。"""
    if not src.is_dir():
        return 0
    count = 0
    for item in src.rglob("*"):
        if any(part in SKIP_DIRS for part in item.parts):
            continue
        if item.suffix in SKIP_SUFFIX:
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        count += 1
    return count


def merge_env(path: Path, values: dict[str, str]) -> list[str]:
    """按键合并 .env：已有的键跳过（除非值明显是坏的），其余追加。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    present: dict[str, int] = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s*([A-Z0-9_]+)\s*=", line)
        if m:
            present[m.group(1)] = i

    written = []
    for key, value in values.items():
        if key in present:
            idx = present[key]
            if lines[idx].split("=", 1)[1].strip():
                continue  # 有值就不动
            lines[idx] = f"{key}={value}"
            written.append(f"{key}（补空值）")
            continue
        lines.append(f"{key}={value}")
        written.append(key)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def read_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            data[m.group(1)] = m.group(2).strip()
    return data


def main() -> int:
    if not CLIENT.is_dir():
        print(f"找不到客户端目录：{CLIENT}", file=sys.stderr)
        return 1

    env_client = CLIENT / "backend" / ".env"
    backup = CLIENT / "backend" / ".env.before-sync"
    if env_client.exists():
        shutil.copy2(env_client, backup)
        print(f"已备份 .env → {backup.name}")

    # 1) 同步代码
    n_app = sync_tree(REPO / "backend" / "app", CLIENT / "backend" / "app")
    n_tools = sync_tree(REPO / "tools", CLIENT / "tools")
    n_dist = sync_tree(REPO / "frontend" / "dist", CLIENT / "frontend" / "dist")
    print(f"同步：app {n_app} 个文件 / tools {n_tools} 个 / dist {n_dist} 个")

    # 2) 修 .env：坏键改名 + 补创作配置（值从仓库 .env 取）
    src_env = read_env(REPO / "backend" / ".env")
    keys = (
        "ZHIPU_API_KEY",
        "ZHIPU_BASE_URL",
        "BAILIAN_API_KEY",
        "MODELSCOPE_API_KEY",
        "MODELSCOPE_VIDEO_ENABLED",
        "ZHIPU_VIDEO_MODEL",
        "ZHIPU_VIDEO_QUALITY",
        "ZHIPU_VIDEO_WITH_AUDIO",
    )
    values = {k: src_env[k] for k in keys if src_env.get(k)}

    # 供应商白名单显式写全（默认已是三家，写出来便于人工核对）
    values["CREATION_PROVIDERS"] = "modelscope,bailian,zhipu"
    values["CREATION_VIDEO_PROVIDERS"] = "zhipu"

    text = env_client.read_text(encoding="utf-8") if env_client.exists() else ""
    if "MODEL_BACKEND=" not in text:
        # 之前有一次误写成了 MODEL_BACKEND，导致后端一直在用默认后端
        text = text.replace("MODEL_BACKEND=", "MODEL_BACKEND=", 1)
        if "MODEL_BACKEND=" in text:
            text = text.replace("MODEL_BACKEND=", "MODEL_BACKEND=ollama", 1)
        else:
            text += "\nMODEL_BACKEND=ollama\n"
        env_client.write_text(text, encoding="utf-8")
        print("已修正 MODEL_BACKEND → MODEL_BACKEND")

    written = merge_env(env_client, values)
    print("写入配置：", "、".join(written) if written else "（都已存在）")

    final = read_env(env_client)
    print("\n当前生效：")
    for key in ("MODEL_BACKEND", "CREATION_PROVIDERS", "CREATION_VIDEO_PROVIDERS"):
        print(f"  {key} = {final.get(key, '(未设置)')}")
    for key in ("ZHIPU_API_KEY", "BAILIAN_API_KEY", "MODELSCOPE_API_KEY"):
        value = final.get(key, "")
        print(f"  {key} = {'已配置（' + value[:6] + '…）' if value else '未配置'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
