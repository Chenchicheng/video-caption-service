"""
POST /api/extract  —  视频文案提取路由
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

router = APIRouter()


class ExtractRequest(BaseModel):
    url: str


class ExtractResponse(BaseModel):
    transcript: str
    description: str
    combined: str
    platform: str


def _detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "bilibili.com" in url_lower or "b23.tv" in url_lower:
        return "bilibili"
    if "douyin.com" in url_lower or "iesdouyin.com" in url_lower:
        return "douyin"
    if "tiktok.com" in url_lower:
        return "tiktok"
    return "unknown"


@router.post("/api/extract", response_model=ExtractResponse)
async def extract_caption(req: ExtractRequest):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")

    platform = _detect_platform(url)

    if platform == "youtube":
        from extractors.youtube import extract
        try:
            result = extract(url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"YouTube 提取失败: {str(e)}")
        return ExtractResponse(**result)

    if platform == "bilibili":
        from extractors.bilibili import extract
        try:
            result = extract(url)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return ExtractResponse(**result)

    # 其他平台后续阶段实现
    raise HTTPException(
        status_code=501,
        detail=f"平台 '{platform}' 尚未支持，当前支持：YouTube、Bilibili",
    )
