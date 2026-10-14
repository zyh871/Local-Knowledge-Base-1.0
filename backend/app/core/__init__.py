"""
基础设施层 —— 不含任何业务逻辑。

依赖铁律：core 只依赖三方库与 utils，禁止 import services / graph / api。

模块：
    config     配置中心（pydantic-settings）
    logging    结构化日志 + trace_id
    exceptions 领域异常族 + 全局处理器
    security   JWT / bcrypt / 用户存储
    redis      Redis 连接池 + 键规范 + TTL + 限流/锁原语（不可用时自动降级）
    deps       FastAPI 依赖注入 provider
"""
