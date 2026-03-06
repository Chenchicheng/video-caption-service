"""
Bilibili 视频文案提取
- description: 视频标题 + 简介（yt-dlp）
- transcript: 字幕（若有，yt-dlp 提取）
"""

import yt_dlp


def extract(url: str) -> dict:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": False,
        "extract_flat": False,
    }

    transcript = ""
    description = ""

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            title = (info.get("title") or "").strip()
            desc = (info.get("description") or "").strip()

            # 合并标题和描述作为 description
            parts = []
            if title:
                parts.append(f"标题：{title}")
            if desc:
                parts.append(desc)
            description = "\n".join(parts)

            # 尝试提取字幕
            subtitles = info.get("subtitles") or {}
            auto_captions = info.get("automatic_captions") or {}
            all_subs = {**auto_captions, **subtitles}  # subtitles 优先

            for lang in ["zh-Hans", "zh-Hant", "zh", "en"]:
                if lang in all_subs:
                    sub_entries = all_subs[lang]
                    # 找 json3 或 srv1 格式
                    for entry in sub_entries:
                        if entry.get("ext") in ("json3", "srv1", "vtt"):
                            sub_url = entry.get("url")
                            if sub_url:
                                transcript = _fetch_subtitle_text(sub_url)
                                if transcript:
                                    break
                if transcript:
                    break

    except Exception as e:
        raise RuntimeError(f"Bilibili 提取失败: {e}")

    combined_parts = []
    if description:
        combined_parts.append(f"【视频描述】\n{description}")
    if transcript:
        combined_parts.append(f"【字幕/语音文字】\n{transcript}")
    combined = "\n\n".join(combined_parts) if combined_parts else description

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "bilibili",
    }


def _fetch_subtitle_text(url: str) -> str:
    """下载字幕文件并提取纯文本"""
    import requests
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")

        if "json" in content_type or url.endswith(".json3"):
            data = resp.json()
            events = data.get("events", [])
            texts = []
            for ev in events:
                segs = ev.get("segs", [])
                line = "".join(s.get("utf8", "") for s in segs).strip()
                if line and line != "\n":
                    texts.append(line)
            return " ".join(texts)

        # vtt / srv1 纯文本处理
        lines = resp.text.splitlines()
        texts = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("WEBVTT") or "-->" in line:
                continue
            if line.isdigit():
                continue
            texts.append(line)
        return " ".join(texts)
    except Exception:
        return ""
