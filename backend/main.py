"""
ClosenAI FastAPI 应用入口。

启动命令：
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import chat, memories
from backend.config import settings

app = FastAPI(
    title="ClosenAI API",
    description="个人成长追踪工具后端 API",
    version="0.1.0",
)

# CORS 配置（开发环境）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(memories.router, prefix="/api/v1", tags=["memories"])


@app.get("/health")
async def health_check():
    """健康检查端点。"""
    return {"status": "ok"}
