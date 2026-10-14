"""自定义模型端点用例。

用户在这里接入任意 **OpenAI 兼容**的第三方服务，填接口地址 + 密钥 + 模型名，
之后问答与工作流都能直接切换过去。

## 三条设计约束

**1. 只支持对话模型，不支持嵌入模型。**
   换嵌入模型意味着向量维度可能变化，而既有的 Chroma 库是按 2048 维建的 ——
   换掉会直接报维度错、必须重建全库（硬约束 5）。
   把一个「填了地址就能用」的入口和「要重建全库」的后果绑在一起太危险，
   所以自定义端点只用于生成，检索侧仍走配置里的 embedding 后端。

**2. 密钥只进不出。**
   写进 `model_repo`，对外一律掩码（`…abcd`）。**响应里绝不出现完整密钥。**
   编辑时密钥留空 = 保持原值，避免用户为了改个名字不得不重新粘贴一遍密钥。

**3. 变更后必须重新注入 providers。**
   providers 里按模型名缓存了客户端实例；不重新注入，
   改了密钥还会继续用旧实例 —— 而且完全不报错。

分层：本模块属 services 层，可依赖 repositories 与 rag。
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlparse

from app.core.config import get_model_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.repositories import model_repo as repo

logger = get_logger(__name__)

__all__ = ["ModelService"]

#: 连通性测试的超时。给 20 秒：跨境端点首包偶尔要 5-10 秒，
#: 但也不能无限等 —— 用户点「测试」时要的是快速反馈。
_TEST_TIMEOUT = 20.0


def mask_key(api_key: str) -> str:
    """把密钥压成可辨认但不可用的形式。

    只保留**末 4 位**：足够让用户确认「填的是哪把钥匙」，
    又不泄漏任何可用于鉴权的部分。
    """
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return f"…{key[-4:]}"


def normalize_base_url(raw: str) -> str:
    """校验并规范化接口地址。

    只去掉结尾斜杠，**不替用户补 `/v1`**：不同网关的路径约定不一样
    （有的在 `/v1`，有的直接是根路径），猜错会得到一个 404，
    而用户完全看不出是自己漏了还是我们加了。
    """
    text = (raw or "").strip()
    if not text:
        raise ValidationError("接口地址不能为空")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError("接口地址必须以 http:// 或 https:// 开头")
    if not parsed.netloc:
        raise ValidationError(f"接口地址不完整：{text}")
    return text.rstrip("/")


class ModelService:
    def __init__(self, *, settings: Any) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    #  配置读取
    # ------------------------------------------------------------------

    @property
    def _cfg(self):
        return get_model_settings()

    def _ensure_enabled(self) -> None:
        if not getattr(self._cfg, "allow_custom_models", True):
            raise ConflictError(
                "当前部署不允许配置自定义模型（ALLOW_CUSTOM_MODELS=false）"
            )

    # ------------------------------------------------------------------
    #  对外视图（掩码）
    # ------------------------------------------------------------------

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "name": item["name"],
            "base_url": item["base_url"],
            "model": item["model"],
            "enabled": item["enabled"],
            "model_id": f"custom:{item['id']}",
            # 只给掩码与「有没有填」，绝不给完整密钥
            "api_key_masked": mask_key(item.get("api_key", "")),
            "has_api_key": bool(item.get("api_key")),
            "last_test_at": item.get("last_test_at"),
            "last_test_ok": item.get("last_test_ok"),
            "last_test_error": item.get("last_test_error"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
        }

    def list_endpoints(self) -> dict[str, Any]:
        return {
            "endpoints": [self._public(item) for item in repo.list_endpoints()],
            "enabled": bool(getattr(self._cfg, "allow_custom_models", True)),
        }

    def get_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        item = repo.get_endpoint(endpoint_id)
        if not item:
            raise NotFoundError("模型端点不存在")
        return self._public(item)

    # ------------------------------------------------------------------
    #  注入 providers
    # ------------------------------------------------------------------

    def sync_providers(self) -> int:
        """把已启用的端点推进 providers，并让受影响的实例缓存失效。

        启动时调一次、每次增删改后再调一次 —— 这是 providers 拿到数据的唯一途径
        （分层规则不允许它自己查库）。
        """
        from app.rag.providers import set_custom_endpoints

        items = [item for item in repo.list_endpoints() if item["enabled"]]
        set_custom_endpoints(items)
        return len(items)

    # ------------------------------------------------------------------
    #  增删改
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        username: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        self._ensure_enabled()
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValidationError("显示名称不能为空")
        clean_model = (model or "").strip()
        if not clean_model:
            raise ValidationError("模型名称不能为空")
        if not (api_key or "").strip():
            raise ValidationError("API 密钥不能为空")

        item = repo.create_endpoint(
            name=clean_name,
            base_url=normalize_base_url(base_url),
            api_key=api_key.strip(),
            model=clean_model,
            created_by=username,
            enabled=enabled,
        )
        self.sync_providers()
        # ⚠️ 日志里不打密钥，也不打 base_url 的查询串
        logger.info(
            f"自定义模型已添加: {clean_name} → {clean_model} @ {item['base_url']}"
        )
        return self._public(item)

    def update(self, endpoint_id: str, **fields: Any) -> dict[str, Any]:
        self._ensure_enabled()
        current = repo.get_endpoint(endpoint_id)
        if not current:
            raise NotFoundError("模型端点不存在")

        patch: dict[str, Any] = {}
        if "name" in fields and fields["name"] is not None:
            clean = str(fields["name"]).strip()
            if not clean:
                raise ValidationError("显示名称不能为空")
            patch["name"] = clean
        if "model" in fields and fields["model"] is not None:
            clean = str(fields["model"]).strip()
            if not clean:
                raise ValidationError("模型名称不能为空")
            patch["model"] = clean
        if "base_url" in fields and fields["base_url"] is not None:
            patch["base_url"] = normalize_base_url(str(fields["base_url"]))
        if "enabled" in fields and fields["enabled"] is not None:
            patch["enabled"] = bool(fields["enabled"])
        if fields.get("api_key"):
            # 留空 = 保持原值：不该为了改个名字被迫重新粘贴一遍密钥
            patch["api_key"] = str(fields["api_key"]).strip()

        if not patch:
            return self._public(current)

        updated = repo.update_endpoint(endpoint_id, **patch)
        if not updated:
            raise NotFoundError("模型端点不存在")
        self.sync_providers()
        # 密钥变了就顺手清掉上一次的测试结论 —— 否则界面会显示一个
        # 属于旧密钥的「测试通过」，误导用户
        if "api_key" in patch or "base_url" in patch:
            repo.set_test_result(endpoint_id, ok=False, error="配置已变更，请重新测试")
            updated = repo.get_endpoint(endpoint_id) or updated
        logger.info(f"自定义模型已更新: {endpoint_id} 字段={sorted(patch)}")
        return self._public(updated)

    def delete(self, endpoint_id: str) -> dict[str, Any]:
        item = repo.get_endpoint(endpoint_id)
        if not item:
            raise NotFoundError("模型端点不存在")
        repo.delete_endpoint(endpoint_id)
        self.sync_providers()
        logger.info(f"自定义模型已删除: {endpoint_id} name={item['name']}")
        return {"success": True, "id": endpoint_id, "name": item["name"]}

    # ------------------------------------------------------------------
    #  连通性测试
    # ------------------------------------------------------------------

    def _probe(self, *, base_url: str, api_key: str, model: str) -> tuple[bool, str]:
        """真实发一次最小请求。返回 (是否成功, 失败原因)。

        刻意走**和正式请求完全相同的构造路径**（`_openai_compatible`）：
        换用裸 httpx 虽然更快，但测通了不代表正式请求也能通 ——
        那这种「测试通过」就是假的。
        """
        from app.rag.providers import _openai_compatible

        try:
            client = _openai_compatible(
                model,
                api_key,
                base_url,
                timeout=_TEST_TIMEOUT,
                max_retries=0,
            )
            client.invoke("ping")
            return True, ""
        except Exception as exc:  # noqa: BLE001 - 任何异常都归为「连不上」
            message = str(exc)
            # 常见错误给出可操作的解释，而不是把上游原文直接甩给用户
            if "401" in message or "Unauthorized" in message or "invalid_api_key" in message:
                return False, "认证失败（401）：请检查 API 密钥"
            if "404" in message or "Not Found" in message:
                return False, "接口返回 404：请检查接口地址是否需要带 /v1 等路径前缀"
            if "model" in message and ("not exist" in message or "not found" in message):
                return False, f"模型不存在：上游不认识「{model}」，请核对模型名称"
            if "timed out" in message or "Timeout" in message:
                return False, f"请求超时（{int(_TEST_TIMEOUT)}s）：地址可能不可达"
            if "Connection" in message or "connect" in message:
                return False, "无法连接到该地址：请检查接口地址与网络"
            return False, message[:300]

    def test_endpoint(self, endpoint_id: str) -> dict[str, Any]:
        """测试已保存端点的连通性，并把结果记下来供界面展示。"""
        item = repo.get_endpoint(endpoint_id)
        if not item:
            raise NotFoundError("模型端点不存在")
        ok, error = self._probe(
            base_url=item["base_url"], api_key=item["api_key"], model=item["model"]
        )
        repo.set_test_result(endpoint_id, ok=ok, error=error)
        logger.info(
            f"自定义模型连通性测试: {endpoint_id} → {'成功' if ok else '失败 ' + error}"
        )
        return self._public(repo.get_endpoint(endpoint_id) or item)

    def test_inline(
        self, *, name: str, base_url: str, api_key: str, model: str
    ) -> dict[str, Any]:
        """保存前先试一次，参数直接来自表单（不落库）。"""
        self._ensure_enabled()
        if not (model or "").strip():
            raise ValidationError("模型名称不能为空")
        if not (api_key or "").strip():
            raise ValidationError("API 密钥不能为空")
        ok, error = self._probe(
            base_url=normalize_base_url(base_url),
            api_key=api_key.strip(),
            model=model.strip(),
        )
        return {"ok": ok, "error": error, "name": (name or "").strip()}
