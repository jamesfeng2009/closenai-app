"""
消息生成服务。

负责在 Reflection Mode 中生成引导式反思消息。
通过 LLM 生成流式的反思引导内容。

TODO: 实现 LLM 调用和流式生成逻辑。
"""
from typing import AsyncGenerator


class MessageGenerator:
    """反思消息生成器。"""

    async def generate_reflection_message(
        self, user_input: str, context: dict | None = None
    ) -> AsyncGenerator[str, None]:
        """
        生成反思引导消息（流式）。

        Args:
            user_input: 用户输入内容
            context: 对话上下文（历史记忆等）

        Yields:
            生成的消息片段
        """
        # TODO: 接入 LLM API，实现流式生成
        yield "TODO: 实现消息生成"


# 依赖注入实例（可替换为工厂模式）
message_generator = MessageGenerator()
