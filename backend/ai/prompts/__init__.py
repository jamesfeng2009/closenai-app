"""AI 能力层的 prompt 与**模型输出契约**（不改权威态，只产出候选内容）。

这里放的是「模型必须怎么答」，不放业务分支：领域校验、算分、归属都在 Go 侧
（``domain.AdmitEvidence``），而 T2-02 的候选组装在 ``backend/ai/mq/handlers/observation.py``。
分层由 ``backend/tests/test_ai_boundary.py`` 判——本包不许 import 仓储／模型／数据库。
"""
