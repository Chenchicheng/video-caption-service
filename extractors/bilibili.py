"""
Bilibili 视频文案提取
直接调用 Bilibili 官方 API，无需登录
- description: 视频标题 + 简介
- transcript: AI 字幕（若有）
"""

import re
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _extract_bvid(url: str) -> str:
    match = re.search(r"BV[A-Za-z0-9]+", url)
    if match:
        return match.group(0)
    raise ValueError(f"无法从 URL 中提取 BV 号: {url}")


def _get_video_info(bvid: str) -> dict:
    """获取视频基本信息（标题、简介、cid）"""
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    resp = requests.get(api, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Bilibili API 错误: {data.get('message')}")
    return data["data"]


def _get_subtitle_url(bvid: str, cid: int) -> str:
    """获取字幕下载地址（AI 字幕）"""
    api = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
    resp = requests.get(api, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        return ""
    subtitles = data.get("data", {}).get("subtitle", {}).get("subtitles", [])
    if not subtitles:
        return ""
    # 优先中文字幕
    for sub in subtitles:
        lang = sub.get("lan", "")
        if "zh" in lang or "ai" in lang:
            url = sub.get("subtitle_url", "")
            if url:
                return "https:" + url if url.startswith("//") else url
    # 取第一个
    url = subtitles[0].get("subtitle_url", "")
    return "https:" + url if url.startswith("//") else url


def _fetch_subtitle_text(url: str) -> str:
    """下载字幕 JSON 并提取纯文本"""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    body = data.get("body", [])
    texts = [item.get("content", "").strip() for item in body if item.get("content")]
    return " ".join(texts)


def extract(url: str) -> dict:
    bvid = _extract_bvid(url)

    try:
        info = _get_video_info(bvid)
    except Exception as e:
        raise RuntimeError(f"获取视频信息失败: {e}")

    title = (info.get("title") or "").strip()
    desc = (info.get("desc") or "").strip()
    cid = info.get("cid", 0)

    desc_parts = []
    if title:
        desc_parts.append(f"标题：{title}")
    if desc and desc != "-":
        desc_parts.append(desc)
    description = "\n".join(desc_parts)

    transcript = ""
    print(f"[bilibili] bvid={bvid}, cid={cid}")

    if cid:
        try:
            sub_url = _get_subtitle_url(bvid, cid)
            print(f"[bilibili] subtitle url={sub_url!r}")
            if sub_url:
                transcript = _fetch_subtitle_text(sub_url)
                print(f"[bilibili] subtitle text length={len(transcript)}")
            else:
                print("[bilibili] 没有找到字幕，进入 Whisper 流程")
        except Exception as e:
            print(f"[bilibili] 获取字幕出错: {e}")
    else:
        print("[bilibili] cid=0，无法获取字幕")

    # 没有字幕时，用 Whisper 语音转文字兜底
    if not transcript and cid:
        print("[bilibili] 开始 Whisper 语音转写...")
        try:
            from extractors.whisper_transcribe import transcribe_bilibili
            transcript = transcribe_bilibili(bvid, cid)
            print(f"[bilibili] Whisper 转写完成，长度={len(transcript)}")
        except Exception as e:
            print(f"[bilibili] Whisper 转写失败: {e}")

    combined_parts = []
    if description:
        combined_parts.append(f"【视频描述】\n{description}")
    try:
        from extractors.transcript_filter import is_transcript_recipe_relevant
        if transcript and is_transcript_recipe_relevant(transcript):
            combined_parts.append(f"【字幕/语音文字】\n{transcript}")
        elif transcript and not is_transcript_recipe_relevant(transcript):
            print(f"[bilibili] 转写疑似歌词/噪音，已排除: {transcript[:40]}...")
    except ImportError:
        if transcript:
            combined_parts.append(f"【字幕/语音文字】\n{transcript}")
    combined = "\n\n".join(combined_parts) if combined_parts else description

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "bilibili",
    }
