"""
Pydantic 数据模型定义。

定义 API 请求/响应的数据结构。
所有模型使用 Pydantic v2 语法。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReflectionChatRequest(BaseModel):
    """反思对话请求。"""

    content: str = Field(..., description="用户输入的反思内容")
    session_id: Optional[str] = Field(None, description="对话会话 ID")
    context: Optional[dict] = Field(None, description="额外上下文信息")


class ReflectionChatResponse(BaseModel):
    """反思对话响应（非流式场景）。"""

    content: str = Field(..., description="生成的反思引导消息")
    session_id: str = Field(..., description="对话会话 ID")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")


class MemoryCreate(BaseModel):
    """记忆创建请求。"""

    content: str = Field(..., description="记忆内容")
    metadata: Optional[dict] = Field(None, description="记忆元数据")


class MemoryResponse(BaseModel):
    """记忆查询响应。"""

    id: str = Field(..., description="记忆 ID")
    content: str = Field(..., description="记忆内容")
    created_at: datetime = Field(..., description="创建时间")
    metadata: Optional[dict] = Field(None, description="记忆元数据")
