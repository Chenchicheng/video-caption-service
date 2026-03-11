"""
视频文案提取服务 - FastAPI 入口
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers.extract import router as extract_router

app = FastAPI(
    title="视频文案提取服务",
    description="从 YouTube、Bilibili、抖音、TikTok 等平台提取视频文案/字幕",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
