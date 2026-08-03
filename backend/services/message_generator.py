"""
消息生成服务 (P0-F01)。

负责 AI 对话消息的生成：流式对话、主动消息四层公式、记忆上下文注入。
"""
from typing import AsyncGenerator, Optional, List, Dict


class MessageGenerator:
    """AI 消息生成器。"""

    # Rule Engine 兜底模板（LLM 失败时使用）
    FALLBACK_TEMPLATES = [
        "今天过得怎么样？有什么想和我聊聊的吗？",
        "我注意到你最近很关注自己的成长，这真的很棒。",
        "你最近提到的那件事，现在进展如何了？",
        "如果今天有什么让你困扰的，我很愿意听你说说。",
    ]

    async def generate_chat_stream(
        self, user_input: str, message_type: str = "reply"
    ) -> AsyncGenerator[str, None]:
        """
        流式对话生成（SSE）。

        根据 user_input 类型选择 LLM：
        - 日常闲聊 → Rule Engine（70% 场景）
        - 深度反思 → Gemini 2.5 Flash（实时流式）
        - 情绪解读 → Gemini 2.5 Flash-Lite / 豆包
        """
        # TODO: 接入 LLM API 流式调用
        # 1. 判断消息复杂度（Rule Engine 路由）
        # 2. 选择 LLM 模型
        # 3. 构建 prompt（含记忆上下文）
        # 4. 流式生成
        response = f"我理解你说的「{user_input[:20]}...」。根据我们之前的对话，"
        for word in response.split():
            yield word + " "

    async def generate_proactive_message(self, user_id: str) -> str:
        """
        主动消息生成（Cron Job 触发）。

        四层公式：
        ① 引用记忆：从 memories 表获取最近相关记忆
        ② 发现模式：从 每日发现（P0-F10）获取对比洞察
        ③ 情绪关联：从 mood_checkins 获取最近情绪
        ④ 互动：生成 Reflection 草稿预填（可选）

        成本控制：
        - 免费用户：Rule Engine 模板 + 数据占位
        - Pro/Pro+ 用户：LLM 个性化生成
        """
        # TODO: 实现主动消息生成
        # 1. 获取记忆上下文
        # 2. 获取每日发现
        # 3. 获取最近情绪
        # 4. 构建四层 prompt
        # 5. 调用 LLM 生成
        return "今天过得怎么样？有什么想记录的吗？"

    async def get_memory_context(self, user_id: str, query: str, n: int = 5) -> List[Dict]:
        """
        获取记忆上下文（用于对话注入）。

        返回：
        1. 最近 n 条相关 current 记忆
        2. 最近 7 天洞察摘要
        3. 性格画像
        """
        # TODO: 调用 memory_service.get_memory_context()
        return []

    def _route_to_engine(self, user_input: str) -> str:
        """
        Rule Engine 路由决策。

        判断消息复杂度，决定使用 Rule Engine 还是 LLM：
        - 简单问候/日常用语 → Rule Engine
        - 深度话题/反思 → LLM
        - 情绪表达 → LLM
        """
        simple_keywords = ["你好", "谢谢", "好的", "嗯", "哦", "哈哈", "是的", "对"]
        if any(kw in user_input for kw in simple_keywords) and len(user_input) < 10:
            return "rule_engine"
        return "llm"


message_generator = MessageGenerator()