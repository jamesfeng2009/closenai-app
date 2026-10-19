"""AI 任务的 handler（R6 起的生产者面）。

``observation.py`` 是 T2-02 交付的那一个（``ai.observation.requested`` → 证据提案）。
剩下三个域（pattern／insight／proactive）仍然**没有** handler：注册表按 D-R5-12 的口径
把未注册的命令永久拒绝并留在 DLQ，那是可观测的记录而不是静默积压。

这一层能 import 什么由 ``backend/tests/test_ai_boundary.py`` 判：仓储／模型／数据库都不许出现，
读库的端口实现住在 ``backend/services/ai_observation.py``，由 ``mq/worker.py`` 的**函数体内**
一处跨层 import 装配（组合根）。
"""
