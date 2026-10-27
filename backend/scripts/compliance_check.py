"""合规「零出域」部署自检。

用途：在把系统交付到政企/涉密/合规环境之前，用一条命令回答
「这套配置到底会不会把数据带出去、有没有留后门式的默认值」。

设计原则：
  * **只读**：除了在数据目录写一个临时探针文件验证可写性，不改任何状态
  * **可编程**：`run_checks()` 返回结构化结果，供 CI 与测试复用
  * **可解释**：每个不通过项都给出 `fix`（具体该改哪个环境变量）

用法：
    python scripts/compliance_check.py
    python scripts/compliance_check.py --json
    python scripts/compliance_check.py --strict   # 警告也算失败（交付闸门）

退出码：0 = 通过（允许有警告）；1 = 存在 fail（或 strict 下的 warn）。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 允许 `python scripts/compliance_check.py` 直接运行时找到 app 包
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: 可完全离线运行的后端（不向上游发送任何内容）
OFFLINE_BACKENDS = ("local", "ollama", "mock")

#: 默认/示例密钥，出现在任何交付环境都视为高危
_INSECURE_SECRETS = {"", "change-me-in-production", "secret", "changeme", "123456"}


@dataclass
class CheckTarget:
    """自检所需的配置快照。

    显式建模而不是直接读 `Settings`，是为了让测试可以构造任意组合，
    不必去 monkeypatch 一大堆环境变量。
    """

    env: str = "dev"
    jwt_secret_key: str = ""
    admin_username: str = ""
    admin_password: str = ""
    redis_fallback: bool = True
    fs_browse: bool = True
    allow_custom_models: bool = True
    model_backend: str = "zhipu"
    cors_origins: list[str] = field(default_factory=list)
    data_path: str = ""

    @classmethod
    def from_settings(cls, settings: Any) -> "CheckTarget":
        security = settings.security
        return cls(
            env=str(getattr(settings, "env", "dev")),
            jwt_secret_key=security.jwt_secret_key,
            admin_username=security.admin_username,
            admin_password=security.admin_password,
            redis_fallback=bool(settings.redis.fallback_to_memory),
            fs_browse=bool(settings.workflow.fs_browse),
            allow_custom_models=bool(settings.model.allow_custom_models),
            model_backend=str(settings.model.backend),
            cors_origins=list(getattr(settings, "cors_origins", []) or []),
            data_path=str(settings.paths.data_path),
        )


def _result(
    check_id: str, title: str, status: str, detail: str, fix: str = ""
) -> dict[str, str]:
    return {
        "id": check_id,
        "title": title,
        "status": status,  # pass | warn | fail
        "detail": detail,
        "fix": fix,
    }


def _check_jwt_secret(target: CheckTarget) -> dict[str, str]:
    secret = (target.jwt_secret_key or "").strip()
    if secret.lower() in _INSECURE_SECRETS:
        return _result(
            "jwt_secret",
            "JWT 签名密钥",
            "fail",
            "使用了默认或空密钥，令牌可被任意伪造",
            "设置 JWT_SECRET_KEY 为 ≥32 位的随机字符串",
        )
    if len(secret) < 24:
        return _result(
            "jwt_secret",
            "JWT 签名密钥",
            "warn",
            f"密钥长度仅 {len(secret)} 位，建议 ≥32 位",
            "用 `python -c \"import secrets;print(secrets.token_urlsafe(48))\"` 生成",
        )
    return _result("jwt_secret", "JWT 签名密钥", "pass", "已设置非默认密钥")


def _check_admin(target: CheckTarget) -> dict[str, str]:
    if not (target.admin_username or "").strip() or not (
        target.admin_password or ""
    ).strip():
        return _result(
            "admin_credentials",
            "管理员凭据",
            "fail",
            "ADMIN_USERNAME / ADMIN_PASSWORD 未设置",
            "在 .env 中配置管理员账号与强口令",
        )
    if len(target.admin_password) < 8:
        return _result(
            "admin_credentials",
            "管理员凭据",
            "warn",
            "管理员口令少于 8 位",
            "改用更强的口令，或接入企业统一认证",
        )
    return _result("admin_credentials", "管理员凭据", "pass", "已配置")


def _check_prod_env(target: CheckTarget) -> dict[str, str]:
    if target.env != "prod":
        return _result(
            "prod_env",
            "运行环境",
            "warn",
            f"当前 APP_ENV={target.env}，错误响应会包含调试信息",
            "交付环境设置 APP_ENV=prod",
        )
    return _result("prod_env", "运行环境", "pass", "APP_ENV=prod")


def _check_redis_fallback(target: CheckTarget) -> dict[str, str]:
    if target.redis_fallback:
        return _result(
            "redis_fallback",
            "Redis 降级",
            "warn",
            "REDIS_FALLBACK_TO_MEMORY=true：Redis 故障时会静默退化为单机语义，"
            "多实例下限流/锁/会话缓存失效",
            "生产环境设置 REDIS_FALLBACK_TO_MEMORY=false",
        )
    return _result("redis_fallback", "Redis 降级", "pass", "已关闭降级")


def _check_fs_browse(target: CheckTarget) -> dict[str, str]:
    if target.fs_browse:
        return _result(
            "fs_browse",
            "本地文件系统浏览",
            "warn",
            "WORKFLOW_FS_BROWSE=true：任何登录用户都可枚举服务器磁盘目录",
            "多用户/对外部署设置 WORKFLOW_FS_BROWSE=false",
        )
    return _result("fs_browse", "本地文件系统浏览", "pass", "已关闭浏览")


def _check_custom_models(target: CheckTarget) -> dict[str, str]:
    if target.allow_custom_models:
        return _result(
            "custom_models",
            "自定义模型端点",
            "warn",
            "ALLOW_CUSTOM_MODELS=true：服务器会携带用户填写的密钥请求任意地址",
            "多用户部署设置 ALLOW_CUSTOM_MODELS=false",
        )
    return _result("custom_models", "自定义模型端点", "pass", "已关闭自定义端点")


def _check_model_egress(target: CheckTarget) -> dict[str, str]:
    backend = (target.model_backend or "").strip().lower()
    if backend in OFFLINE_BACKENDS:
        return _result(
            "model_egress",
            "模型数据出域",
            "pass",
            f"MODEL_BACKEND={backend}，推理在本机完成",
        )
    return _result(
        "model_egress",
        "模型数据出域",
        "warn",
        f"MODEL_BACKEND={backend}：问答内容会发送到云端上游",
        "零出域场景切换 MODEL_BACKEND=ollama（或 local）并本地拉起模型",
    )


def _check_cors(target: CheckTarget) -> dict[str, str]:
    wildcard = [o for o in target.cors_origins if (o or "").strip() == "*"]
    if wildcard:
        return _result(
            "cors",
            "跨域白名单",
            "warn",
            "CORS_ORIGINS 含通配符 *，任意站点都可携带凭据访问",
            "改为显式域名列表",
        )
    if not target.cors_origins:
        return _result(
            "cors", "跨域白名单", "warn", "未配置显式来源", "配置 CORS_ORIGINS"
        )
    return _result(
        "cors", "跨域白名单", "pass", f"已限制 {len(target.cors_origins)} 个来源"
    )


def _check_data_writable(target: CheckTarget) -> dict[str, str]:
    if not (target.data_path or "").strip():
        return _result(
            "data_writable", "数据目录", "warn", "未配置数据目录", "设置 DATA_PATH"
        )
    data_dir = Path(target.data_path)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=str(data_dir), prefix=".compliance_", suffix=".tmp", delete=True
        ) as fh:
            fh.write("ok")
    except Exception as exc:  # noqa: BLE001
        return _result(
            "data_writable",
            "数据目录",
            "fail",
            f"数据目录不可写：{exc}",
            f"修正目录权限或改用可写路径（当前 {data_dir}）",
        )
    return _result("data_writable", "数据目录", "pass", f"可写：{data_dir}")


def run_checks(target: CheckTarget) -> dict[str, Any]:
    """执行全部检查，返回结构化报告。"""
    checks = [
        _check_jwt_secret(target),
        _check_admin(target),
        _check_prod_env(target),
        _check_redis_fallback(target),
        _check_fs_browse(target),
        _check_custom_models(target),
        _check_model_egress(target),
        _check_cors(target),
        _check_data_writable(target),
    ]
    summary = {"pass": 0, "warn": 0, "fail": 0}
    for item in checks:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return {
        "ok": summary["fail"] == 0,
        "summary": summary,
        "checks": checks,
    }


_STATUS_MARK = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append("合规「零出域」部署自检")
    lines.append("=" * 68)
    for item in report["checks"]:
        mark = _STATUS_MARK.get(item["status"], "[????]")
        lines.append(f"{mark} {item['title']}: {item['detail']}")
        if item["status"] != "pass" and item.get("fix"):
            lines.append(f"       → {item['fix']}")
    s = report["summary"]
    lines.append("-" * 68)
    lines.append(f"通过 {s['pass']} / 警告 {s['warn']} / 失败 {s['fail']}")
    lines.append("结论：" + ("可交付" if report["ok"] else "存在阻断项，不可交付"))
    lines.append("=" * 68)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="合规「零出域」部署自检")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--strict", action="store_true", help="把警告也视为失败（交付闸门）"
    )
    args = parser.parse_args(argv)

    from app.core.config import get_settings

    report = run_checks(CheckTarget.from_settings(get_settings()))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))

    if report["summary"]["fail"] > 0:
        return 1
    if args.strict and report["summary"]["warn"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
