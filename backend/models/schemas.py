"""
Pydantic 数据模型定义。

定义 API 请求/响应的数据结构。
所有模型使用 Pydantic v2 语法。
"""
from datetime import datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, Field


# ═══ Chat (P0-F01) ═══

class ChatStreamRequest(BaseModel):
    """SSE 流式对话请求。"""
    content: str = Field(..., description="用户输入内容")
    message_type: Literal["reply", "mood_checkin"] = Field("reply", description="消息类型")


class ChatHistoryResponse(BaseModel):
    """对话历史响应。"""
    id: str
    role: Literal["user", "ai"]
    content: str
    created_at: datetime


# ═══ Memory (P0-F02) ═══

class MemoryCreate(BaseModel):
    """记忆创建请求。"""
    content: str = Field(..., description="记忆内容")
    category: Literal["values", "preference", "relationship", "goal", "factual"] = Field(
        "factual", description="记忆类别"
    )
    source: Literal["self", "others", "hypothetical"] = Field(
        "self", description="记忆来源"
    )
    conviction: float = Field(0.5, ge=0.0, le=1.0, description="信念强度")
    metadata: Optional[dict] = Field(None, description="记忆元数据")


class MemoryResponse(BaseModel):
    """记忆查询响应。"""
    id: str
    user_id: str
    content: str
    category: str
    status: str  # current | superseded | reverted
    source: str
    conviction: float
    created_at: datetime
    superseded_at: Optional[datetime] = None
    transition_reason: Optional[str] = None


class MemorySearchRequest(BaseModel):
    """语义搜索请求。"""
    query: str = Field(..., description="搜索查询")
    limit: int = Field(10, description="返回结果数")
    include_superseded: bool = Field(False, description="是否包含已替代记忆")


class MemoryVersionRequest(BaseModel):
    """记忆版本化请求。"""
    memory_id: str = Field(..., description="原记忆 ID")
    transition_type: Literal["gradual", "sudden", "revert"] = Field(..., description="转变类型")
    reason: Optional[str] = Field(None, description="转变原因")
    emotion_context: Optional[dict] = Field(None, description="情绪上下文")


# ═══ Mood Check-in (P0-F03) ═══

class MoodCheckinRequest(BaseModel):
    """情绪签到请求。"""
    emoji: str = Field(..., description="表情符号: 😊😌😰😢😡😴")
    note: Optional[str] = Field(None, description="可选文字备注")


class MoodCheckinResponse(BaseModel):
    """情绪签到响应。"""
    id: str
    mood_emoji: str
    mood_label: str
    ai_interpretation: str
    streak_updated: int
    created_at: datetime


class MoodHistoryResponse(BaseModel):
    """签到历史响应。"""
    checkins: List[MoodCheckinResponse]


class MoodTrendResponse(BaseModel):
    """情绪趋势响应。"""
    labels: List[str]  # 日期标签
    values: List[int]  # 情绪值（1-5）
    summary: str       # 趋势总结


# ═══ Growth Dashboard (P0-F07) ═══

class GrowthMetrics(BaseModel):
    """成长指标。"""
    emotion: str           # emoji
    emotion_change: float  # 变化百分比
    social: str
    social_change: str
    confidence: str
    confidence_change: str
    stress: str
    stress_change: float
    curiosity: str
    curiosity_change: str
    goals: str
    goals_change: str


class GrowthDashboardResponse(BaseModel):
    """成长仪表盘响应。"""
    date: str
    greeting: str
    streak_count: int
    relationship_level: str
    level_label: str           # 初识 L1 | 知己 L2 | ...
    level_progress: float      # 0.0-1.0
    chat_count: int
    today_mood: Optional[str]
    growth_metrics: GrowthMetrics
    mood_timeline: List[str]   # 14 天热力图颜色
    weekly_insight: Optional[str]
    yesterday_record: Optional[str]
    mbti_type: Optional[str] = None   # 性格画像入口展示


class WeeklyInsightResponse(BaseModel):
    """本周洞察响应。"""
    level: str                 # L2 Pattern
    content: str
    period: str                # 本周日期范围


# ═══ Reflection Card (P0-F04) ═══

class ReflectionGenerateRequest(BaseModel):
    """Reflection 草稿生成请求。"""
    context: Optional[str] = Field(None, description="可选补充上下文")


class ReflectionCreate(BaseModel):
    """保存 Reflection 请求。"""
    content: str = Field(..., description="Reflection 内容")


class ReflectionResponse(BaseModel):
    """Reflection 响应。"""
    id: str
    content: str
    related_memory_ids: Optional[List[str]] = None
    created_at: datetime


class ReflectionDraftResponse(BaseModel):
    """Reflection 草稿响应。"""
    draft: str
    related_memory_ids: Optional[List[str]] = None


# ═══ Personality (P0-F05) ═══

class PersonalityProfileResponse(BaseModel):
    """性格画像响应。"""
    id: str
    mbti_type: Optional[str]       # INTP 等
    mbti_label: Optional[str]      # 逻辑学家
    observations: List[str]
    unique_traits: Optional[str]
    version: int
    refresh_quota_remaining: int
    cold_start_progress: Optional[float]  # 0.0-1.0, 冷启动进度
    cold_start_stage: Optional[str]      # 破冰 | 观察 | 探索 | 揭示 | 运用
    created_at: datetime


class PersonalityRefreshResponse(BaseModel):
    """性格画像刷新响应。"""
    success: bool
    profile: Optional[PersonalityProfileResponse]
    message: str


# ═══ User (P0-F04) ═══

class UserProfileResponse(BaseModel):
    """用户信息响应。"""
    id: str
    name: Optional[str]
    mbti_type: Optional[str]
    language: Optional[str] = None
    streak_count: int
    relationship_level: str
    chat_count: int
    tier: str  # free | pro | pro_plus
    daily_quota_used: int
    daily_quota_total: int
    ai_consent: bool = False  # AI 数据共享同意状态（合规 5.1.2(i)）


class AIConsentUpdate(BaseModel):
    """AI 数据共享同意更新（5.1.2(i)：用户可随时同意/撤回）。"""
    consent: bool


class UserSettingsUpdate(BaseModel):
    """用户设置更新请求。"""
    name: Optional[str] = None
    chat_style: Optional[str] = None
    push_time: Optional[str] = None   # HH:MM 格式
    timezone: Optional[str] = None    # IANA 时区名（如 Asia/Shanghai），禁止 "UTC+8" 偏移格式
    language: Optional[str] = None    # 15 种语言码之一
    push_frequency: Optional[Literal["daily", "important", "off"]] = None  # 推送频率
    push_enabled: Optional[bool] = None  # 推送总开关
    mbti_type: Optional[str] = None  # 引导流程中用户已知的 MBTI 类型（P1-F05）


class PushTokenRequest(BaseModel):
    """推送 Token 注册请求。"""
    token: str = Field(..., description="设备推送 Token")
    platform: Literal["ios", "android"] = Field(..., description="平台")


# ═══ Discovery (P0-F10) ═══

class DailyDiscoveryResponse(BaseModel):
    """每日发现响应。"""
    has_discovery: bool
    content: Optional[str]           # 发现内容
    comparison_type: Optional[str]   # 对比类型
    generated_at: datetime


# ═══ IAP ═══

class ReceiptVerifyRequest(BaseModel):
    """收据校验请求。"""
    receipt_data: str = Field(..., description="Base64 编码的收据")
    product_id: str = Field(..., description="产品 ID")
    is_sandbox: bool = Field(False, description="是否 Sandbox 环境")


class ReceiptVerifyResponse(BaseModel):
    """收据校验响应。"""
    valid: bool
    tier: Optional[str] = None       # free | pro | pro_plus
    expires_at: Optional[datetime] = None
    message: str


class SubscriptionStatusResponse(BaseModel):
    """订阅状态响应。"""
    tier: str
    is_active: bool
    expires_at: Optional[datetime]
    product_id: Optional[str]
    auto_renew: bool