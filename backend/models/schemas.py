"""
Pydantic 数据模型定义。

定义 API 请求/响应的数据结构。
所有模型使用 Pydantic v2 语法。
"""
import re
from datetime import date, datetime
from typing import Optional, List, Literal, Dict
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.config import settings
from backend.core.time_utils import local_today


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
    similarity: Optional[float] = None  # 语义搜索命中时的相似度
    regressed: bool = False  # 本次检索触发了回归识别（[回归信念] 标注）


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
    new_content: Optional[str] = Field(None, description="演变后的新信念内容（gradual/sudden）；缺省沿用原内容")


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
    # 深度解读（连续 3 天同情绪 / 连续 2 天负面时由 LLM 生成；未触发或生成失败为 None）
    deep_interpretation: Optional[str] = None


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


class MilestoneItem(BaseModel):
    """里程碑徽章（PRD P0-F05：连续 7/30/100/365 天）。"""
    milestone_type: str        # streak_7 | streak_30 | streak_100 | streak_365
    days: int                  # 达成所需连续天数
    achieved: bool
    achieved_at: Optional[datetime] = None


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
    milestones: List[MilestoneItem] = []   # 里程碑徽章（含未达成占位）
    streak_freeze_count: int = 0           # 剩余 Streak 冻结券


class WeeklyInsightResponse(BaseModel):
    """本周洞察响应。"""
    level: str                 # L2 Pattern
    content: Optional[str]     # 无已生成周报时为 None（GET 只读，不触发生成）
    period: str                # 本周日期范围


# ═══ Reflection Card (P0-F04) ═══

class ReflectionGenerateRequest(BaseModel):
    """Reflection 草稿生成请求。"""
    context: Optional[str] = Field(None, description="可选补充上下文")


class ReflectionCreate(BaseModel):
    """保存 Reflection 请求。"""
    content: str = Field(..., description="Reflection 内容")
    related_memory_ids: Optional[List[str]] = Field(None, description="关联的记忆 ID 列表（来自草稿）")


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
    cold_start_stage: Optional[int]       # 阶段下标 0-4（破冰|观察|探索|揭示|运用），前端按下标高亮，避免多语言文案比较
    zodiac: Optional[str] = None          # 太阳星座 key（aries 等，由生日推导；前端按 i18n 翻译）
    enneagram_type: Optional[str] = None  # 九型人格编号（1-9，用户主动输入）
    created_at: datetime


class PersonalityRefreshResponse(BaseModel):
    """性格画像刷新响应。"""
    success: bool
    profile: Optional[PersonalityProfileResponse]
    message: str


# ── 对话式评估（PRD P0-F06：8 题问答）──

class AssessmentQuestion(BaseModel):
    """评估题目（按用户语言下发）。"""
    index: int                 # 0 起
    total: int                 # 总题数
    dimension: str             # EI | SN | TF | JP
    text: str
    options: Dict[str, str]    # {"a": ..., "b": ..., "neutral": ...}


class AssessmentStateResponse(BaseModel):
    """评估状态响应。"""
    status: str                # not_started | in_progress | completed
    current_index: int         # 下一题下标（= 已答题数）
    total: int
    question: Optional[AssessmentQuestion] = None


class AssessmentAnswerRequest(BaseModel):
    """评估答题请求：快捷选项（option）或自由输入（answer）二选一。"""
    option: Optional[Literal["a", "b", "neutral"]] = None
    answer: Optional[str] = None

    @model_validator(mode="after")
    def _require_option_or_answer(self):
        if not self.option and not (self.answer and self.answer.strip()):
            raise ValueError("option 与 answer 至少提供一个")
        return self


class AssessmentAnswerResponse(BaseModel):
    """评估答题响应。"""
    completed: bool
    next_question: Optional[AssessmentQuestion] = None
    profile: Optional[PersonalityProfileResponse] = None  # 完成时返回生成的画像
    message: str = ""


class ComboReportResponse(BaseModel):
    """Pro 组合报告响应（16 型人格 × 星座 × 九型交叉解读）。"""
    content: str
    cached: bool               # 本月已生成过则命中缓存
    generated_at: datetime
    personality_type: Optional[str] = None
    zodiac: Optional[str] = None
    enneagram_type: Optional[str] = None


# ═══ User (P0-F04) ═══

class UserProfileResponse(BaseModel):
    """用户信息响应。"""
    id: str
    name: Optional[str]
    username: Optional[str] = None  # 登录用户名（账号安全页展示）
    mbti_type: Optional[str]
    language: Optional[str] = None
    streak_count: int
    relationship_level: str
    chat_count: int
    tier: str  # free | pro | pro_plus
    daily_quota_used: int
    daily_quota_total: int
    ai_consent: bool = False  # AI 数据共享同意状态（合规 5.1.2(i)）
    streak_freeze_count: int = 0  # 剩余 Streak 冻结券（PRD P0-F05）
    push_time: Optional[str] = None  # 主动消息时间 HH:MM（None 时后端按 09:00 调度）


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
    birthday: Optional[str] = None   # 生日 YYYY-MM-DD（P0-F06：推导太阳星座）
    enneagram_type: Optional[str] = None  # 九型人格编号 1-9（P0-F06：用户主动输入）

    @field_validator("birthday")
    @classmethod
    def _validate_birthday(cls, v: Optional[str]) -> Optional[str]:
        """校验 YYYY-MM-DD 格式（日期范围校验在 _validate_birthday_past 中按用户时区进行）。"""
        if v is None:
            return v
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError(f"birthday 必须为 YYYY-MM-DD 格式: {v}")
        return v

    @field_validator("enneagram_type")
    @classmethod
    def _validate_enneagram(cls, v: Optional[str]) -> Optional[str]:
        """校验九型人格编号为 1-9。"""
        if v is None:
            return v
        if v not in {str(n) for n in range(1, 10)}:
            raise ValueError(f"enneagram_type 必须为 1-9: {v}")
        return v

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: Optional[str]) -> Optional[str]:
        """校验 IANA 时区名（对应错误码 INVALID_TIMEZONE 40004）。"""
        if v is None:
            return v
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"无效的 IANA 时区名: {v}")
        return v

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: Optional[str]) -> Optional[str]:
        """校验语言码在 15 种支持语言集合内（对应错误码 INVALID_LANGUAGE 40003）。"""
        if v is None:
            return v
        if v not in settings.supported_languages:
            raise ValueError(f"不支持的语言码: {v}")
        return v

    @field_validator("push_time")
    @classmethod
    def _validate_push_time(cls, v: Optional[str]) -> Optional[str]:
        """校验 HH:MM 格式且时分合法。"""
        if v is None:
            return v
        if not re.fullmatch(r"\d{2}:\d{2}", v):
            raise ValueError(f"push_time 必须为 HH:MM 格式: {v}")
        hour, minute = int(v[:2]), int(v[3:])
        if hour > 23 or minute > 59:
            raise ValueError(f"push_time 时分越界: {v}")
        return v

    @model_validator(mode="after")
    def _validate_birthday_past(self):
        """生日必须为过去日期（按用户本地时区判定「今日」，禁 naive date.today()）。

        timezone 缺省时 local_today 回退 UTC（保守安全）；同请求带 timezone 时按用户本地日。
        """
        if self.birthday is None:
            return self
        d = date.fromisoformat(self.birthday)
        if d >= local_today(self.timezone):
            raise ValueError("birthday 必须是过去的日期")
        return self


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
    is_trial: bool = False           # 是否免费试用中（P1-F02 §4）
    message: str


class SubscriptionStatusResponse(BaseModel):
    """订阅状态响应。"""
    tier: str
    is_active: bool
    expires_at: Optional[datetime]
    product_id: Optional[str]
    auto_renew: bool
    is_trial: bool = False           # 是否免费试用中（到期前 24h 推送提醒）


# ═══ Auth ═══

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_USERNAME_RE = r"^[A-Za-z0-9_.-]+$"


class RegisterRequest(BaseModel):
    """邮箱 + 用户名 + 密码注册。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    username: str = Field(..., min_length=3, max_length=50, pattern=_USERNAME_RE)
    password: str = Field(..., min_length=8, max_length=128)
    language: str = "en"


class LoginRequest(BaseModel):
    """用户名/邮箱 + 密码登录。"""
    identifier: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)


class SendCodeRequest(BaseModel):
    """发送邮箱验证码。purpose: register|login|reset。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    purpose: Literal["register", "login", "reset"]
    language: str = "en"


class LoginCodeRequest(BaseModel):
    """邮箱验证码登录（未注册自动建账号）。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    code: str = Field(..., min_length=4, max_length=8)
    language: str = "en"


class AppleLoginRequest(BaseModel):
    """Apple 登录（Sign in with Apple）。"""
    identity_token: str = Field(..., min_length=1)
    auth_code: Optional[str] = None
    language: str = "en"


class RefreshRequest(BaseModel):
    """刷新 token。"""
    refresh_token: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    """忘记密码（发重置验证码；防枚举，统一返回成功）。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    language: str = "en"


class ResetPasswordRequest(BaseModel):
    """重置密码（验证码 + 新密码）。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    code: str = Field(..., min_length=4, max_length=8)
    new_password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    """修改密码（旧 + 新；改密后吊销全部 refresh，强制重新登录）。"""
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class UpdateUsernameRequest(BaseModel):
    """修改用户名。"""
    username: str = Field(..., min_length=3, max_length=50, pattern=_USERNAME_RE)


class AuthUserResponse(BaseModel):
    """认证返回的用户摘要。"""
    id: str
    email: Optional[str] = None
    username: Optional[str] = None
    name: Optional[str] = None
    tier: str = "free"
    email_verified: bool = False


class AuthResponse(BaseModel):
    """登录/注册/刷新成功响应。"""
    access_token: str
    refresh_token: str
    user: AuthUserResponse