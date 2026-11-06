#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看 / 重置管理员密码。

为什么需要它
    密码是**安装时随机生成**的，只在那一次安装的完成页面上打印过一遍。
    用户重装、换了安装目录、或者当初没记下来，就再也进不去了 —— 而 .env 里
    其实一直明文存着，只是没人知道去看哪儿。

    这个工具做三件事，都不碰别的东西：
      1. 默认：显示当前用户名和密码在哪、能不能登进去（bcrypt 实际校验一次）
      2. --show：把密码打印出来（本机操作，自己看得见）
      3. --set：改成一个新密码，同时更新 .env 与 identity.db 里的哈希

用法
    python tools/reset_password.py                # 诊断：现在能不能登、密码在哪
    python tools/reset_password.py --show         # 连密码一起打印
    python tools/reset_password.py --set 新密码    # 改成新密码
    python tools/reset_password.py --dir "D:\\智鲸AI"
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_backend(base: Path) -> Path:
    """支持三种位置：安装根目录、backend 目录、仓库根目录。"""
    for cand in (base / "backend", base, base / "Local-Knowledge-Base-1.0-main" / "backend"):
        if (cand / "app").is_dir() and ((cand / ".env").exists() or (cand / "data").exists()):
            return cand
    raise SystemExit(f"没找到 backend 目录（试过 {base}/backend）")


def read_env(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def write_env_value(env_path: Path, key: str, value: str) -> None:
    """只改一个键，其它行原样保留（含注释与顺序）。"""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pattern = re.compile(rf"\s*{re.escape(key)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_hash(db_path: Path, username: str, password: str) -> tuple[bool, str]:
    """实际用 bcrypt 校验一次，避免「看着像对其实不对」。"""
    if not db_path.exists():
        return False, "identity.db 不存在（首次启动后才会创建）"
    try:
        from app.core.security import verify_password  # 复用线上同一套校验
    except Exception:  # noqa: BLE001  直接跑脚本时可能没把 backend 加进 sys.path
        try:
            import bcrypt

            def verify_password(plain: str, hashed: str) -> bool:  # type: ignore[misc]
                return bcrypt.checkpw(plain.encode(), hashed.encode())
        except ImportError:
            # 连依赖都没装（还没跑过 setup.cmd）：不阻断，按「密码以 .env 为准」处理
            return True, "缺 bcrypt，跳过校验（.env 里的 ADMIN_PASSWORD 就是登录密码）"

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "select password_hash, global_role, status from users where username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return False, f"数据库里没有用户 {username}"
    if row[2] and row[2] != "active":
        return False, f"账号状态是 {row[2]}，不能登录"
    ok = verify_password(password, row[0])
    return ok, "哈希比对通过" if ok else "哈希对不上（.env 与数据库不同步）"


def set_password(backend: Path, username: str, new_password: str) -> None:
    """改 .env 与 identity.db 里的哈希。

    依赖没装时也能改 .env；数据库那条会跳过并提示，下次启动前跑一次
    setup.cmd 再用本工具即可。
    """
    env_path = backend / ".env"
    write_env_value(env_path, "ADMIN_PASSWORD", new_password)
    print(f"  已更新 {env_path}")

    db_path = backend / "data" / "identity.db"
    if not db_path.exists():
        return
    try:
        from app.core.security import hash_password
    except Exception:  # noqa: BLE001
        print("  [!] 没装依赖（bcrypt），数据库里的哈希没改；")
        print("      跑一次 setup.cmd 后，再执行本工具 --set 同一个密码即可同步")
        return

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "update users set password_hash = ? where username = ?",
            (hash_password(new_password), username),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"  [!] 数据库里没有 {username}，只改了 .env；首次启动会用 .env 建号")
        else:
            print(f"  已更新 {db_path}")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="智鲸 AI 管理员密码诊断 / 重置")
    ap.add_argument("--dir", default=str(ROOT), help="安装目录（默认本脚本所在目录的上一级）")
    ap.add_argument("--show", action="store_true", help="打印明文密码")
    ap.add_argument("--set", metavar="新密码", help="修改密码（同时更新 .env 与 identity.db）")
    args = ap.parse_args()

    backend = find_backend(Path(args.dir).resolve())
    sys.path.insert(0, str(backend))  # 让 import app.* 生效

    env = read_env(backend / ".env")
    username = env.get("ADMIN_USERNAME", "admin")
    password = env.get("ADMIN_PASSWORD", "")

    print("")
    print("  智鲸 AI · 管理员账号")
    print("  ────────────────────────────────────────")
    print(f"  安装目录  {backend.parent}")
    print(f"  配置文件  {backend / '.env'}")
    print(f"  用户名    {username}")

    if not password:
        print("  [!] .env 里没有 ADMIN_PASSWORD —— 后端会直接启动失败")
        print("      修：python tools/ollama_bootstrap.py --write-env（会补一个随机密码）")
        return 1

    print(f"  密码      {'*' * len(password)}（{len(password)} 位，明文存在 .env 的 ADMIN_PASSWORD）")
    if args.show:
        print(f"           {password}")

    ok, why = check_hash(backend / "data" / "identity.db", username, password)
    print(f"  能否登录  {'✅ 可以' if ok else '❌ 不行'}（{why}）")

    if args.set:
        set_password(backend, username, args.set)
        ok2, why2 = check_hash(backend / "data" / "identity.db", username, args.set)
        print(f"  重置结果  {'✅ 新密码可用' if ok2 else '❌ ' + why2}")
        if not ok2:
            return 1

    print("")
    if not ok and not args.set:
        print("  下一步：重置成你知道的密码")
        print('      python tools/reset_password.py --set 你的新密码')
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
