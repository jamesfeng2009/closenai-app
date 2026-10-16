"""
AI 能力层（V3 12「Python AI Runtime」在 Python 侧的落点）。

职责：只做「模型调用与内容判定」这一类无状态能力——LLM 补全/流式/嵌入、
内容安全判定，以及 R3 起的 gRPC server（`rpc/`）。

禁止：本层不许 import `backend.routers`、`backend.repositories`、`backend.models`，
也不许直接持有数据库会话（`SessionLocal` / `get_db`）。闸在
`backend/tests/test_ai_boundary.py`，改这条边界要先过那道闸。

为什么只有两个文件：`message_generator`／`memory_service` 等今天同时是业务编排与写面，
搬进来当场违反上面那道禁令 ⇒ 能力层拆分随 O3／R5，本目录只做第一轮归堆。

依赖方向：`backend/ai/` ← `backend/services/` ← `backend/routers/`。
"""
