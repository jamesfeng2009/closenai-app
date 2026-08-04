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
    # Gemini（保留）
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_lite_model: str = "gemini-2.5-flash-lite"
    # OpenAI 兼容自定义
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    openai_lite_model: str = ""

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
        "env_file": BASE_DIR / ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
