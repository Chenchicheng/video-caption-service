"""
YouTube 视频文案提取
- transcript: 字幕/语音转文字（youtube-transcript-api）
- description: 视频描述文字（yt-dlp）
"""

import re
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import yt_dlp


def _extract_video_id(url: str) -> str:
    """从 YouTube URL 中提取 video_id"""
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"无法从 URL 中提取 YouTube video_id: {url}")


def extract_transcript(video_id: str) -> str:
    """
    提取字幕，优先中文，其次英文，再次自动生成字幕。
    返回纯文本，失败返回空字符串。
    """
    ytt = YouTubeTranscriptApi()
    preferred_langs = ["zh-Hans", "zh-Hant", "zh", "en"]

    try:
        transcript_list = ytt.list(video_id)
    except Exception:
        return ""

    # 优先手动字幕
    for lang in preferred_langs:
        try:
            fetched = transcript_list.find_manually_created_transcript([lang]).fetch()
            return " ".join(snip.text for snip in fetched).strip()
        except Exception:
            continue

    # 其次自动生成字幕
    for lang in preferred_langs:
        try:
            fetched = transcript_list.find_generated_transcript([lang]).fetch()
            return " ".join(snip.text for snip in fetched).strip()
        except Exception:
            continue

    # 最后取第一个可用语言
    try:
        transcripts = list(transcript_list)
        if transcripts:
            fetched = transcripts[0].fetch()
            return " ".join(snip.text for snip in fetched).strip()
    except Exception:
        pass

    return ""


def extract_description(url: str) -> str:
    """使用 yt-dlp 提取视频描述文字。失败返回空字符串。"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return (info.get("description") or "").strip()
    except Exception:
        return ""


def extract(url: str) -> dict:
    """
    主入口：提取 YouTube 视频的 transcript + description + combined
    """
    video_id = _extract_video_id(url)
    transcript = extract_transcript(video_id)
    description = extract_description(url)

    parts = []
    if description:
        parts.append(f"【视频描述】\n{description}")
    if transcript:
        parts.append(f"【字幕/语音文字】\n{transcript}")
    combined = "\n\n".join(parts)

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "youtube",
    }
