"""验收脚本统一的管理员凭据来源。

**不要在脚本里写明文口令** —— 仓库里出现过的明文口令等于凭据泄露，
任何人拿到仓库（含历史提交、fork、外包交付包）就能以管理员身份登录。

用法：

    from _creds import admin_credentials
    USER, PWD = admin_credentials()

凭据来源优先级：环境变量 > 仓库根 `.env` > `backend/.env`。
两者都取不到时直接退出，避免用错的口令反复登录失败却看不出原因。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_env_files() -> None:
    """补一次 .env 装载；缺失 dotenv 或文件时静默跳过。"""
    try:
        from dotenv import load_dotenv
    except Exception:  # noqa: BLE001 - 脚本环境可能没装 python-dotenv
        return
    for candidate in (_REPO_ROOT / ".env", _REPO_ROOT / "backend" / ".env"):
        if candidate.exists():
            # override=False：已存在的环境变量优先
            load_dotenv(candidate, override=False)


def admin_credentials() -> tuple[str, str]:
    """返回 (用户名, 口令)。缺失时抛 SystemExit 并给出可执行的提示。"""
    if not os.environ.get("ADMIN_PASSWORD"):
        _load_env_files()

    username = (os.environ.get("ADMIN_USERNAME") or "").strip()
    password = (os.environ.get("ADMIN_PASSWORD") or "").strip()
    if not username or not password:
        print(
            "缺少管理员凭据：请在 backend/.env 中设置 ADMIN_USERNAME / ADMIN_PASSWORD，"
            "或用环境变量传入。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return username, password


if __name__ == "__main__":  # pragma: no cover - 手工自检
    user, _pwd = admin_credentials()
    print(f"credentials OK for user={user} (password hidden)")
