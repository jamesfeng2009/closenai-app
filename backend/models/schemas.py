"""
Pydantic 数据模型定义。

定义 API 请求/响应的数据结构。
所有模型使用 Pydantic v2 语法。
"""
import re
from datetime import datetime, time
from typing import Optional, List, Literal, Dict
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.config import settings
from backend.models.user import CONVERSATION_STYLES


# ═══ Chat (P0-F01) ═══

class ChatStreamRequest(BaseModel):
    """SSE 流式对话请求。"""
    content: str = Field(..., max_length=4000, description="用户输入内容")
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
    content: str = Field(..., max_length=4000, description="记忆内容")
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
    limit: int = Field(10, ge=1, le=100, description="返回结果数")
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
    note: Optional[str] = Field(None, max_length=1000, description="可选文字备注")


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
    # 关系等级晋升负载（PRD P0-F05：升级当次签到返回，含新解锁能力；未升级为 None）
    level_up: Optional[dict] = None


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
    name: Optional[str] = None  # 徽章名称（按请求语言本地化，如 一周坚持 / 7-Day Streak）
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
    content: str = Field(..., max_length=4000, description="Reflection 内容")
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
    """Pro 组合报告响应（16 型人格 × 九型交叉解读）。"""
    content: str
    cached: bool               # 本月已生成过则命中缓存
    generated_at: datetime
    personality_type: Optional[str] = None
    enneagram_type: Optional[str] = None


# ── 九型人格渐进式评估（PRD P1-F03：36 题，可跨会话断点续答）──

class EnneagramQuestion(BaseModel):
    """九型评估题目（按用户语言下发；不暴露题目归属类型，防刷分）。"""
    index: int                 # 0 起
    total: int                 # 总题数（36）
    text: str
    options: Dict[str, str]    # {"a": 符合我, "b": 不符合我, "neutral": 不确定}


class EnneagramResultResponse(BaseModel):
    """九型评估结果（完成时 / 查询状态且已有类型时返回）。"""
    enneagram_type: str        # 1-9
    type_label: str            # 按用户语言的类型名（如 探索者 / Investigator）
    combo_name: Optional[str] = None   # MBTI×九型组合名（有 16 型画像且命中关联表时）
    combo_mbti: Optional[str] = None   # 组合对应的 16 型类型


class EnneagramStateResponse(BaseModel):
    """九型评估状态响应。"""
    status: str                # not_started | in_progress | completed
    current_index: int         # 下一题下标（= 已答题数）
    total: int
    question: Optional[EnneagramQuestion] = None
    result: Optional[EnneagramResultResponse] = None


class EnneagramAnswerResponse(BaseModel):
    """九型评估答题响应。"""
    completed: bool
    next_question: Optional[EnneagramQuestion] = None
    result: Optional[EnneagramResultResponse] = None  # 完成时返回
    message: str = ""


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
    conversation_style: str = "warm"  # AI 对话风格（P1-F04）：warm | rational | philosophical


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
    conversation_style: Optional[str] = None  # AI 对话风格（P1-F04）：warm | rational | philosophical
    enneagram_type: Optional[str] = None  # 九型人格编号 1-9（P0-F06：用户主动输入）

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

    @field_validator("conversation_style")
    @classmethod
    def _validate_conversation_style(cls, v: Optional[str]) -> Optional[str]:
        """校验对话风格枚举（P1-F04；非法值 400，与 language 校验同路径）。"""
        if v is None:
            return v
        if v not in CONVERSATION_STYLES:
            raise ValueError(f"不支持的对话风格: {v}")
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
    priority: Optional[str] = None   # 价值分级：high | medium | low（P0-F10 融入主动消息）
    generated_at: datetime


# ═══ IAP ═══

class ReceiptVerifyRequest(BaseModel):
    """收据校验请求。"""
    receipt_data: str = Field(..., description="Base64 编码的收据")
    product_id: str = Field(..., description="产品 ID")
    is_sandbox: bool = Field(False, description="是否 Sandbox 环境")


class LifeArchiveEntitlement(BaseModel):
    """一笔 Life Archive 一次性买断权益（M6，与订阅 tier 无关）。"""
    product_id: str
    scope: Literal["1y", "all", "legacy"]
    transaction_id: str
    purchase_date: Optional[datetime] = None
    environment: Optional[str] = None


class ReceiptVerifyResponse(BaseModel):
    """收据校验响应。"""
    valid: bool
    # 收据内交易类型：subscription（自动续期订阅）| entitlement（一次性买断）。
    # 买断响应必须显式区分，否则前端会把"买了 Life Archive"渲染成"订阅已生效"。
    kind: Literal["subscription", "entitlement"] = "subscription"
    tier: Optional[str] = None       # free | pro | pro_plus（买断时保持用户当前订阅档位）
    expires_at: Optional[datetime] = None
    is_trial: bool = False           # 是否免费试用中（P1-F02 §4）
    product_id: Optional[str] = None
    scope: Optional[Literal["1y", "all", "legacy"]] = None  # 买断最高档
    entitlements: List[LifeArchiveEntitlement] = []
    message: str


class SubscriptionStatusResponse(BaseModel):
    """订阅状态响应。"""
    tier: str
    is_active: bool
    expires_at: Optional[datetime]
    product_id: Optional[str]
    auto_renew: bool
    is_trial: bool = False           # 是否免费试用中（到期前 24h 推送提醒）


class EntitlementsResponse(BaseModel):
    """Life Archive 买断权益查询响应（P0-2 / M6）。"""
    entitlements: List[LifeArchiveEntitlement] = []
    owned_scopes: List[Literal["1y", "all", "legacy"]] = []
    best_scope: Optional[Literal["1y", "all", "legacy"]] = None


class LifeArchiveExportRequest(BaseModel):
    """Life Archive 导出请求。

    scope 只放宽导出窗口，不放宽授权：持有 legacy 可导出 all/1y（向下兼容），
    只持有 1y 不能导出 all。
    """
    scope: Literal["1y", "all", "legacy"] = Field("all", description="导出档位")
    language: str = Field("en", max_length=10, description="语言")
    regenerate: bool = Field(False, description="忽略缓存重新生成（同档位同周期幂等）")


# ═══ Onboarding 性格初读卡 (P1-B01 / 架构方案 §二 M1) ═══

class FirstReadAnswer(BaseModel):
    """单道快问的答案（q = 题目 id，option = 选项 id）。

    选项 id 与前端 OnboardingScreen 的 QUICK_QUESTIONS 对齐（模板库在后端
    `backend/data/first_read/{zh,en}.json`）；后端只认模板内已有的 id/文案，
    未知项在 first_read_service 里跳过并回退 filler。
    """
    q: str = Field(..., max_length=30, description="题目 id：conflict / uncertainty / night")
    option: str = Field(..., max_length=40, description="选项 id")


class FirstReadRequest(BaseModel):
    """初读卡生成请求（幂等 upsert，重复提交以最后一次为准）。"""
    answers: List[FirstReadAnswer] = Field(default_factory=list, max_length=10)
    persona_type: Optional[str] = Field(
        None, description="引导期自选性格类型（16 型；D 级标签，仅描述用户自己）"
    )
    language: str = Field("en", max_length=10, description="生成语言（落库后按此复现）")

    @field_validator("persona_type")
    @classmethod
    def _normalize_persona(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        upper = v.strip().upper()
        if not re.fullmatch(r"[A-Z]{4}", upper):
            raise ValueError(f"非法的性格类型: {v}")
        return upper


class FirstReadResponse(BaseModel):
    """初读卡响应（card_payload 供前端渲染与分享复用）。"""
    assertions: List[str]
    summary: str = ""
    closer: str = ""
    card_payload: Dict = Field(default_factory=dict)


class MemoryCardRequest(BaseModel):
    """记忆卡分享（M4）：只接记忆 id，正文取该条原话；归属由 repository 强制。"""
    memory_id: str = Field(..., max_length=36)


class JourneyAckRequest(BaseModel):
    """剧本事件已读回执（红点消费；非本人事件按 40402 处理）。"""
    event_id: str = Field(..., max_length=36)


# ═══ Auth ═══

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_USERNAME_RE = r"^[A-Za-z0-9_.-]+$"


def _validate_supported_language(v: str) -> str:
    """校验语言码在支持集合内（对应错误码 INVALID_LANGUAGE 40003）。

    与 UserSettingsUpdate._validate_language 共用 settings.supported_languages，
    供注册/发码/验证码登录/Apple 登录/忘记密码等认证入口请求的 language 字段复用。
    """
    if v not in settings.supported_languages:
        raise ValueError(f"不支持的语言码: {v}")
    return v


class RegisterRequest(BaseModel):
    """邮箱 + 用户名 + 密码注册。

    ref：分享链接带来的推荐码（M4 安装归因）；格式非法在 auth_service
    侧静默丢弃，不在这里 422——归因丢了不该挡住注册。
    """
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    username: str = Field(..., min_length=3, max_length=50, pattern=_USERNAME_RE)
    password: str = Field(..., min_length=8, max_length=128)
    language: str = "en"
    ref: Optional[str] = Field(None, max_length=16, description="分享推荐码（M4）")

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        return _validate_supported_language(v)


class LoginRequest(BaseModel):
    """用户名/邮箱 + 密码登录。"""
    identifier: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1)


class SendCodeRequest(BaseModel):
    """发送邮箱验证码。purpose: register|login|reset。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    purpose: Literal["register", "login", "reset"]
    language: str = "en"

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        return _validate_supported_language(v)


class LoginCodeRequest(BaseModel):
    """邮箱验证码登录（未注册自动建账号）。

    ref：仅首次自动建账号时作为安装归因落库，已注册用户忽略。
    """
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    code: str = Field(..., min_length=4, max_length=8)
    language: str = "en"
    ref: Optional[str] = Field(None, max_length=16, description="分享推荐码（M4）")

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        return _validate_supported_language(v)


class AppleLoginRequest(BaseModel):
    """Apple 登录（Sign in with Apple）。

    ref：仅首次登录建账号时作为安装归因落库，已注册用户忽略。
    """
    identity_token: str = Field(..., min_length=1)
    auth_code: Optional[str] = None
    language: str = "en"
    ref: Optional[str] = Field(None, max_length=16, description="分享推荐码（M4）")

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        return _validate_supported_language(v)


class RefreshRequest(BaseModel):
    """刷新 token。"""
    refresh_token: str = Field(..., min_length=1)


class ForgotPasswordRequest(BaseModel):
    """忘记密码（发重置验证码；防枚举，统一返回成功）。"""
    email: str = Field(..., pattern=_EMAIL_RE, max_length=255)
    language: str = "en"

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        return _validate_supported_language(v)


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


# ═══ Analytics (PostHog 中继) ═══

class AnalyticsEvent(BaseModel):
    """前端上报的单条分析事件。

    - event: 事件名（如 screen_view / checkin / chat_sent）
    - properties: 行为属性（后端按白名单匿名化，剔除内容字段）
    - occurred_at: 真实发生时间（UTC，离线延迟事件用此时间保证口径准确）
    """
    event: str
    properties: Optional[Dict] = None
    occurred_at: datetime


class AnalyticsBatchRequest(BaseModel):
    """前端本地 analytics_events 队列批量上报请求。"""
    events: List[AnalyticsEvent] = Field(..., max_length=100)


