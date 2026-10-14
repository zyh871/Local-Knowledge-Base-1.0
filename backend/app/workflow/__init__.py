"""AI 工作流域内逻辑（计划生成、文件上下文组装等）。

本层位于 `core/utils/document/tools/rag` 之上、`services` 之下，**不依赖**
graph / services / api / repositories —— 它只做纯逻辑与文本处理，因此可以
在没有任何图、数据库、网络的环境下完整单测。
"""
