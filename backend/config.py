"""
ClosenAI 后端配置管理。

通过环境变量读取配置，提供合理的默认值。
"""
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings

# backend 目录绝对路径（保证从任何位置运行都能定位 .env）
BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """应用配置。通过环境变量或 .env 文件加载。"""

    # 应用配置
    app_name: str = "ClosenAI"
    app_tagline: str = "记录你的人生 · 理解你的模式 · 陪你一起成长"
    debug: bool = False
    # 运行环境：development（默认，允许 X-User-Id 设备身份透传）/ production（强制 JWT Bearer 鉴权）
    environment: str = "development"

    # API 配置
    api_v1_prefix: str = "/api/v1"

    # CORS
    cors_origins: List[str] = ["http://localhost:8081", "http://localhost:19006"]

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./closenai.db"
    supabase_url: str = ""
    supabase_service_key: str = ""

    # JWT（access + refresh 共用 secret，仅有效期不同）
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 30

    # 邮件（Resend，邮箱验证码 / 密码重置）
    resend_api_key: str = ""
    email_from: str = "ClosenAI <noreply@closenai.app>"

    # Apple 登录（验 identityToken 的 audience；App Store Connect → Services ID）
    apple_client_id: str = ""

    # LLM（多 Provider）
    # 可切换：dashscope / doubao / gemini / openai
    llm_provider: str = "dashscope"
    # DashScope（通义千问）
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen3.6-flash"
    dashscope_lite_model: str = "qwen-flash"
    dashscope_embedding_model: str = "text-embedding-v4"
    # 豆包（火山方舟）
    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str = ""
    doubao_lite_model: str = ""
    doubao_embedding_model: str = ""  # 火山方舟 embedding 端点 id/模型名（切换 doubao 时必填）
    # Gemini（保留）
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_lite_model: str = "gemini-2.5-flash-lite"
    gemini_embedding_model: str = "text-embedding-004"
    # OpenAI 兼容自定义
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    openai_lite_model: str = ""
    openai_embedding_model: str = "text-embedding-3-small"

    # 支持语言（多语言，15 种：13 种首发 + vi 越南语 + km 高棉语）
    supported_languages: List[str] = [
        "en", "zh", "es", "ko", "ja", "ru",
        "id", "ms", "th", "de", "fr", "it", "pt",
        "vi", "km",
    ]

    # 向量维度（memories.embedding 使用 512 维 halfvec，与 DashScope text-embedding-v4 对齐）
    # 注意：v4（Qwen3-Embedding，100+ 语种含 vi/km，MRL 支持 128-2048 截断）与 v3 向量空间不兼容；
    # 512 维为 MRL 推荐截断点之一，召回损失 <1%，存储/索引较 768 维 float4 节省 ~66%（Supabase 免费版 500MB 约束下的性价比最优解）
    embedding_dim: int = 512

    # Redis (Upstash)
    redis_url: str = ""
    redis_token: str = ""

    # QStash
    qstash_token: str = ""
    qstash_url: str = ""
    # QStash 回调验签密钥（Signing Keys，支持轮换：current 验不过再试 next）
    qstash_current_signing_key: str = ""
    qstash_next_signing_key: str = ""
    # 后端公网基础 URL（QStash publish 的回调目标，如 https://your-app.vercel.app）
    backend_base_url: str = ""

    # 推送
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_private_key: str = ""
    apns_topic: str = ""
    # FCM legacy server key（已废弃，仅作 HTTP v1 未配置时的回退）
    fcm_server_key: str = ""
    # FCM HTTP v1 service account JSON（内容为单行 JSON 串或 JSON 文件路径；配置后优先走 v1）
    fcm_service_account_json: str = ""

    # IAP
    app_store_shared_secret: str = ""
    app_store_env: str = "sandbox"  # sandbox | production
    # 试用到期前提醒提前量（小时，PRD P1-F02 §4：7 天免费试用，到期前 24 小时提醒）
    trial_remind_hours: int = 24

    # 分享（P1-F04 §5：分享落地 → App Store 下载页）
    app_store_url: str = "https://apps.apple.com/app/closenai/id6740000000"
    # 分享卡深链站点（M4 / 决策点 3）：https://closenai.app/r/{referral_code}
    # Universal Links 生效需两件事同时成立：站点在 `site/` 部署后于
    # /.well-known/apple-app-site-association 直出 JSON（见 site/_headers），
    # 且 App 侧带 applinks 权限（frontend/app.json 的 ios.associatedDomains）。
    site_url: str = "https://closenai.app"

    # 配额（每日额度判定统一由 services/quota_service.py 执行。双口径，PRD P1-F02 §2：
    # ① 对话轮数 = chat/stream 的 user 消息数；② 统一池 = 主动消息 + 签到解读。
    # pro / pro_plus 为高限（≈不限，防异常调用打爆 LLM 成本的熔断上限））
    free_daily_messages: int = 5       # 免费统一池每日额度（主动消息 + 签到解读）
    free_daily_chat_rounds: int = 10   # 免费每日对话轮次
    pro_daily_messages: int = 100      # Pro 统一池每日高限
    pro_plus_daily_messages: int = 1000  # Pro+ 统一池每日高限
    pro_daily_chat_rounds: int = 100   # Pro 每日对话轮次高限
    pro_plus_daily_chat_rounds: int = 1000  # Pro+ 每日对话轮次高限
    free_personality_refreshes: int = 3  # 免费性格画像刷新（总计）
    pro_personality_refreshes: int = 5   # Pro 性格画像刷新（每月）

    # 定时任务
    cron_secret: str = ""
    health_check_interval: int = 5       # /health 保热间隔（分钟）
    # 事件后主动跟进窗口（天，PRD P0-F07）：记录创建满 start 天且未超 end 天时，
    # 每日 cron 生成「上次你提到...现在怎么样了？」式跟进消息（默认 3-7 天）
    follow_up_window_start_days: int = 3
    follow_up_window_end_days: int = 7

    # PostHog（行为分析 + 错误追踪，Cloud·EU；生产才启用，key 为空则服务内 no-op）
    posthog_api_key: str = ""
    posthog_host: str = "https://eu.i.posthog.com"

    model_config = {
        "env_file": BASE_DIR / ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
