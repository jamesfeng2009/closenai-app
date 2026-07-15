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
    debug: bool = False

    # API 配置
    api_v1_prefix: str = "/api/v1"

    # CORS
    cors_origins: List[str] = ["http://localhost:8081", "http://localhost:19006"]

    # 数据库（按需配置）
    database_url: str = "sqlite+aiosqlite:///./closenai.db"

    # LLM 配置（按需配置）
    llm_api_key: str = ""
    llm_model: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()
