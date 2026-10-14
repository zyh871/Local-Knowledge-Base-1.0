"""API 层：路由与依赖装配。

本层只做三件事：声明契约 → 调 service → 返回 schema。
禁止在此写业务逻辑。
"""

from app.api.deps import (
    AgentServiceDep,
    ConversationServiceDep,
    DocumentServiceDep,
    FeedbackServiceDep,
    LongTextServiceDep,
    ProfileServiceDep,
    QAServiceDep,
    ShareServiceDep,
    SystemServiceDep,
)

__all__ = [
    "QAServiceDep",
    "DocumentServiceDep",
    "ConversationServiceDep",
    "ShareServiceDep",
    "ProfileServiceDep",
    "FeedbackServiceDep",
    "AgentServiceDep",
    "SystemServiceDep",
    "LongTextServiceDep",
]
