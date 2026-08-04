"""
核心基础设施层。

包含：
- errors  错误码体系 + 业务异常
- response 统一响应格式
- logging  日志系统（trace_id / user_id 透传）
- middleware 中间件（trace_id 生成 + 全局异常处理）

禁止：在此层中编写任何业务逻辑。
"""