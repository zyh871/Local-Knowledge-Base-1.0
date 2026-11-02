"""为浏览器验证预置一个管理员令牌。

不打印密码：直接从配置里取管理员身份，签一个短期 JWT 写到临时文件，
供 Playwright 脚本注入 localStorage。仅用于本地端到端验证。
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import create_access_token, get_security_settings  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "_verify_auth.json"

cfg = get_security_settings()
username = cfg.admin_username
token = create_access_token(
    {"sub": "usr_admin", "username": username}, timedelta(hours=2)
)
OUT.write_text(
    json.dumps({"token": token, "username": username}, ensure_ascii=False),
    encoding="utf-8",
)
print(f"token written: {OUT.name} (user={username})")
