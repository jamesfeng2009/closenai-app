"""R5 · `backend/ai/mq/`：AI 运行时的 RabbitMQ 面（消费命令、回投候选）。

这一层存在的唯一理由是 V3 `17:62-74` 那八步：consume → validate metadata → decode →
validate contract → execute → produce candidate → publish → **ACK input**。八步里除了
第五步（真跑 AI，归 R6 的注册表），其余都是**传输与契约**，不是业务编排——所以这个包
不许 import 仓储、模型或 HTTP 面（`backend/tests/test_ai_boundary.py` 的四条禁令同样管它）。

与 Go 侧的分工（D-R5-3）：**拓扑由 Go 独占主动声明**，本包一律 passive（只断言存在）。
这不是洁癖——R4 在真 broker 上撞过 `PRECONDITION_FAILED - inequivalent arg 'x-message-ttl'`，
两份声明就是一套拓扑的两个事实源，而第二份一定漂。
"""
