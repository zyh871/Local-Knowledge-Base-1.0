"""合规自检脚本测试。

只测 `run_checks()` 的判定逻辑：它是交付闸门，判定错一次就会
要么放行不合规配置、要么误报阻断交付，两种代价都高。
"""

from __future__ import annotations

from scripts.compliance_check import CheckTarget, format_text, run_checks


def _good(tmp_path) -> CheckTarget:
    return CheckTarget(
        env="prod",
        jwt_secret_key="k" * 48,
        admin_username="admin",
        admin_password="Str0ng-Passw0rd",
        redis_fallback=False,
        fs_browse=False,
        allow_custom_models=False,
        model_backend="ollama",
        cors_origins=["http://localhost"],
        data_path=str(tmp_path / "data"),
    )


def _by_id(report) -> dict[str, dict]:
    return {item["id"]: item for item in report["checks"]}


def test_clean_config_passes(tmp_path):
    report = run_checks(_good(tmp_path))
    assert report["ok"] is True
    assert report["summary"]["fail"] == 0
    assert report["summary"]["warn"] == 0


def test_default_secret_is_blocking(tmp_path):
    target = _good(tmp_path)
    target.jwt_secret_key = "change-me-in-production"
    report = run_checks(target)
    assert report["ok"] is False
    assert _by_id(report)["jwt_secret"]["status"] == "fail"


def test_short_secret_warns(tmp_path):
    target = _good(tmp_path)
    target.jwt_secret_key = "short-secret"
    report = run_checks(target)
    assert report["ok"] is True
    assert _by_id(report)["jwt_secret"]["status"] == "warn"


def test_cloud_backend_warns_on_egress(tmp_path):
    target = _good(tmp_path)
    target.model_backend = "zhipu"
    report = run_checks(target)
    assert _by_id(report)["model_egress"]["status"] == "warn"
    assert report["ok"] is True


def test_risky_switches_warn(tmp_path):
    target = _good(tmp_path)
    target.fs_browse = True
    target.allow_custom_models = True
    target.redis_fallback = True
    report = run_checks(target)
    checks = _by_id(report)
    assert checks["fs_browse"]["status"] == "warn"
    assert checks["custom_models"]["status"] == "warn"
    assert checks["redis_fallback"]["status"] == "warn"


def test_missing_admin_is_blocking(tmp_path):
    target = _good(tmp_path)
    target.admin_password = ""
    report = run_checks(target)
    assert _by_id(report)["admin_credentials"]["status"] == "fail"


def test_text_report_marks_blocking(tmp_path):
    target = _good(tmp_path)
    target.jwt_secret_key = ""
    text = format_text(run_checks(target))
    assert "不可交付" in text
    assert "[FAIL]" in text
