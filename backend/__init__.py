"""
ClosenAI 后端应用包。

后端分层架构（四层）：
    routers/       — HTTP 路由层：只处理 HTTP 请求/响应，不含业务逻辑
    services/      — 业务逻辑层：依赖注入 repositories，实现核心业务规则
    repositories/  — 数据访问层：只与数据库交互，不含业务逻辑
    models/        — 数据模型层：Pydantic 模型 + SQLAlchemy ORM 定义，不含任何逻辑

依赖方向：routers -> services -> repositories -> models
"""
