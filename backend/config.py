"""
ClosenAI 后端配置管理。

通过环境变量读取配置，提供合理的默认值。
"""
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置。通过环境变量或 .env 文件加载。"""

    # 应用配置
    app_name: str = "ClosenAI"
    app_tagline: str = "记录你的人生 · 理解你的模式 · 陪你一起成长"
    debug: bool = False

    # API 配置
    api_v1_prefix: str = "/api/v1"

    # CORS
    cors_origins: List[str] = ["http://localhost:8081", "http://localhost:19006"]

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./closenai.db"
    supabase_url: str = ""
    supabase_service_key: str = ""

    # JWT
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    # LLM
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_lite_model: str = "gemini-2.5-flash-lite"
    doubao_api_key: str = ""
    doubao_model: str = ""

    # Redis (Upstash)
    redis_url: str = ""
    redis_token: str = ""

    # QStash
    qstash_token: str = ""
    qstash_url: str = ""

    # 推送
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_private_key: str = ""
    apns_topic: str = ""
    fcm_server_key: str = ""

    # IAP
    app_store_shared_secret: str = ""
    app_store_env: str = "sandbox"  # sandbox | production

    # 配额
    free_daily_messages: int = 5       # 免费每日 AI 主动消息
    free_daily_chat_rounds: int = 10   # 免费每日对话轮次
    free_personality_refreshes: int = 3  # 免费性格画像刷新（总计）
    pro_personality_refreshes: int = 5   # Pro 性格画像刷新（每月）

    # 定时任务
    cron_secret: str = ""
    health_check_interval: int = 5       # /health 保热间隔（分钟）

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
