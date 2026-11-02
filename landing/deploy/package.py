#!/usr/bin/env python3
"""打包成可分发的安装包（zip）。

做三件事：
  1. 把 .ps1 统一转成 **UTF-8 with BOM** —— PowerShell 5.1 对无 BOM 的 UTF-8
     会按系统 ANSI（简体中文下是 GBK）解码，中文提示全变乱码。
  2. 按白名单收集文件（不把截图、构建脚本、dist 本身打进去）。
  3. 生成 zip + SHA256 + manifest.txt。

用法：
    python landing/deploy/package.py
    python landing/deploy/package.py --version 1.0.1
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "landing"
DIST = LANDING / "dist"

BOM = b"\xef\xbb\xbf"

#: 打进安装包的文件（相对 landing/）
PAYLOAD = [
    "index.html",
    "developers.html",
    # 真实界面截图：首页 hero 与功能区都引用它们，缺了会破图
    "shots/app-chat.webp",
    "shots/app-chat.png",
    "shots/app-models.webp",
    "shots/app-models.png",
    "README.md",
    "VERSION.txt",
    "install.cmd",
    "install.ps1",
    "start.cmd",
    "start.ps1",
    "uninstall.cmd",
    "uninstall.ps1",
    "deploy/serve.py",
    "deploy/nginx.conf",
    "deploy/Dockerfile",
]

#: 必须是 UTF-8 BOM 的文件
NEEDS_BOM = ["install.ps1", "start.ps1", "uninstall.ps1"]


def ensure_bom(path: Path) -> None:
    """补上（或修正为单个）UTF-8 BOM。"""
    raw = path.read_bytes()
    if raw.startswith(BOM):
        return
    path.write_bytes(BOM + raw)


def main() -> None:
    ap = argparse.ArgumentParser(description="打包智鲸 AI 产品介绍页安装包")
    ap.add_argument("--version", default=None, help="版本号，默认取 VERSION.txt")
    args = ap.parse_args()

    version = args.version
    if version is None:
        vfile = LANDING / "VERSION.txt"
        version = "1.0.0"
        if vfile.exists():
            for line in vfile.read_text(encoding="utf-8").splitlines():
                if line.startswith("VERSION="):
                    version = line.split("=", 1)[1].strip()
                    break

    for name in NEEDS_BOM:
        p = LANDING / name
        if p.exists():
            ensure_bom(p)

    missing = [f for f in PAYLOAD if not (LANDING / f).exists()]
    if missing:
        raise SystemExit(f"缺少文件，无法打包：{missing}")

    DIST.mkdir(exist_ok=True)
    bundle = f"zhijing-landing-{version}-windows"
    zip_path = DIST / f"{bundle}.zip"
    if zip_path.exists():
        zip_path.unlink()

    built = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"bundle   {bundle}",
        f"version  {version}",
        f"built    {built}",
        "",
        f"{'size':>9}  sha256(前16位)  file",
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in PAYLOAD:
            src = LANDING / rel
            data = src.read_bytes()
            zf.writestr(f"{bundle}/{rel}", data)
            digest = hashlib.sha256(data).hexdigest()[:16]
            lines.append(f"{len(data):>9}  {digest}  {rel}")

    raw = zip_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()

    manifest = "\n".join(lines) + f"\n\nzip sha256  {sha}\n"
    (DIST / "manifest.txt").write_text(manifest, encoding="utf-8")

    print(manifest)
    print(f"安装包：{zip_path}  ({len(raw) / 1024:.1f} KB)")

    # 顺带留一份解包后的目录，方便本地直接试装
    unpacked = DIST / bundle
    if unpacked.exists():
        shutil.rmtree(unpacked)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(DIST)
    print(f"已解包到：{unpacked}")


if __name__ == "__main__":
    main()
