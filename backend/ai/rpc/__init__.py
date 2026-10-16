"""gRPC 进程入口（R3）。

放这里的东西只有一件事：把 `contracts/proto/closenai_ai_rpc.proto` 的 service 实现成
「decode → 调 `backend/ai/*` 或 service → encode → return」的 handler。

禁令（与上层 `backend/ai/__init__.py` 同源，由 `test_ai_boundary.py` 与 R3 的「handler
零业务分支」闸共同守着）：
- 不写业务分支：判断口径一律从 service/能力函数取，handler 里不许出现第二套；
- 不碰数据库：权威态写入属 Go（O3），本目录唯一允许的 import 例外是 `backend.gen.*`
  （protobuf 生成物）；
- purpose 留痕由**被调用的那一层**产生，handler 里直接调 `llm.*` 会让
  `ai_generation_runs.purpose` 变成 `server.embed`，用户语义当场失真。
"""
