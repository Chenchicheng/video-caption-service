"""
抖音视频文案提取
- description: 页面 meta（og:title、og:description）+ API desc
- transcript: 视频直链 -> Whisper 音频转写，或 VLM 视频帧分析
- 支持 iesdouyin.com、douyin.com 分享链接
"""

import re
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.iesdouyin.com/",
}


def _extract_video_id(url: str) -> str | None:
    """从抖音分享链接提取视频 ID"""
    # 格式: .../video/7583913948572110120/ 或 .../note/xxx
    m = re.search(r"/video/(\d+)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"item_id=(\d+)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{15,})/", url)
    if m:
        return m.group(1)
    return None


def _extract_meta_content(html: str) -> list[str]:
    """提取 og:title、og:description 等 meta"""
    parts = []
    pattern = re.compile(
        r'<meta\s+(?:property|name)=["\'](?:og:title|og:description|twitter:title|twitter:description|description)["\']\s+content=["\']([^"\']*)["\']',
        re.I
    )
    seen = set()
    for m in pattern.finditer(html):
        content = m.group(1).replace("&nbsp;", " ").replace("&amp;", "&").strip()
        if len(content) > 10 and content not in seen:
            seen.add(content)
            parts.append(content)
    return parts


def _fetch_item_info(video_id: str) -> dict | None:
    """调用抖音 API 获取视频详情（desc、video play_addr）"""
    api = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}"
    try:
        resp = requests.get(api, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("item_list") or []
        if items:
            return items[0]
    except Exception as e:
        print(f"[douyin] API 请求失败: {e}")
    return None


def _extract_video_url_from_api(item: dict) -> str | None:
    """从 API 返回的 item 中提取视频直链，去水印"""
    try:
        video = item.get("video") or {}
        play_addr = video.get("play_addr") or {}
        url_list = play_addr.get("url_list") or []
        if url_list:
            url = url_list[0]
            # 去水印：playwm -> play
            url = url.replace("/playwm/", "/play/")
            return url
    except Exception:
        pass
    return None


def _extract_video_url_from_html(html: str) -> str | None:
    """从页面 HTML 提取视频直链（og:video、内嵌 URL）"""
    # og:video meta
    for pat in [
        r'<meta\s+(?:property|name)=["\']og:video["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+\.mp4[^"\']*)["\']\s+(?:property|name)=["\']og:video["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            url = m.group(1).strip()
            if "douyin" in url or "iesdouyin" in url or ".mp4" in url:
                return url.replace("/playwm/", "/play/")
    # 内嵌 play_addr url_list
    m = re.search(r'"url_list"\s*:\s*\["((?:https?:[^"]+))"', html)
    if m:
        url = m.group(1).replace("\\/", "/").replace("/playwm/", "/play/")
        if ".mp4" in url or "play" in url:
            return url
    return None


def _transcribe_douyin(video_url: str) -> str:
    """抖音专用：下载 mp4 -> ffmpeg 提取音频 -> SiliconFlow 转写"""
    try:
        from extractors.whisper_transcribe import transcribe_douyin
        return transcribe_douyin(video_url)
    except Exception as e:
        print(f"[douyin] Whisper 转写失败: {e}")
        return ""


def extract_with_video_url(url: str, video_url: str) -> dict:
    """
    客户端已提供视频直链，直接转写，仍抓取页面获取 description
    """
    print(f"[douyin] 使用客户端传入的视频直链: {video_url[:60]}...")
    url = url.strip()
    description = ""
    html = ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        meta_parts = _extract_meta_content(html)
        if meta_parts:
            description = "【页面摘要】\n" + "\n".join(meta_parts)
    except Exception as e:
        print(f"[douyin] 请求页面失败: {e}")

    # 尝试 API 补充 desc
    vid = _extract_video_id(url)
    if vid:
        item = _fetch_item_info(vid)
        if item and item.get("desc"):
            desc_text = item.get("desc", "").strip()
            if desc_text and desc_text not in description:
                description = (description + "\n\n【视频描述】\n" + desc_text).strip()

    if not description:
        description = "（视频内容）"

    transcript = _transcribe_douyin(video_url)
    if transcript:
        print(f"[douyin] Whisper 转写完成，长度={len(transcript)}")

    # 过滤：ASR 是否菜谱相关
    if transcript:
        try:
            from extractors.transcript_filter import is_transcript_recipe_relevant
            if not is_transcript_recipe_relevant(transcript):
                print(f"[douyin] 转写疑似歌词/噪音，已排除")
                transcript = ""
        except ImportError:
            pass

    # ASR 无效时尝试 VLM
    vision_text = ""
    if len(transcript) < 30:
        desc_has_recipe = any(
            kw in description
            for kw in ("食材", "步骤", "做法", "克", "适量", "翻炒", "调料", "配料", "焯水")
        )
        if not (len(description) >= 300 and desc_has_recipe):
            try:
                from extractors.vision_extract import extract_recipe_from_video_frames
                vision_text = extract_recipe_from_video_frames(
                    video_url,
                    referer="https://www.iesdouyin.com"
                )
                if vision_text:
                    print(f"[douyin] VLM 分析完成，长度={len(vision_text)}")
            except Exception as e:
                print(f"[douyin] VLM 分析失败: {e}")

    combined_parts = [f"【视频描述】\n{description}"] if description else []
    if transcript:
        combined_parts.append(f"【字幕/语音文字】\n{transcript}")
    if vision_text:
        combined_parts.append(f"【视频画面分析】\n{vision_text}")
    combined = "\n\n".join(combined_parts) if combined_parts else description

    return {
        "transcript": transcript or vision_text,
        "description": description,
        "combined": combined,
        "platform": "douyin",
    }


def extract(url: str) -> dict:
    """从抖音链接提取文案：页面 meta + API desc + 视频转写"""
    url = url.strip()
    print(f"[douyin] 请求 URL: {url[:80]}...")

    video_id = _extract_video_id(url)
    if not video_id:
        raise RuntimeError("无法从链接中提取视频 ID，请检查是否为有效的抖音分享链接")

    description = ""
    video_url = None

    # 1. 抓取页面获取 meta
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        meta_parts = _extract_meta_content(html)
        if meta_parts:
            description = "【页面摘要】\n" + "\n".join(meta_parts)
        video_url = _extract_video_url_from_html(html)
    except Exception as e:
        print(f"[douyin] 页面请求失败: {e}")

    # 2. API 获取 desc 和视频直链
    item = _fetch_item_info(video_id)
    if item:
        if item.get("desc"):
            desc_text = item.get("desc", "").strip()
            if desc_text and desc_text not in description:
                description = (description + "\n\n【视频描述】\n" + desc_text).strip()
        if not video_url:
            video_url = _extract_video_url_from_api(item)

    if not description:
        description = "（视频内容）"

    transcript = ""
    if video_url:
        print(f"[douyin] 找到视频直链: {video_url[:60]}...")
        transcript = _transcribe_douyin(video_url)
        if transcript:
            print(f"[douyin] Whisper 转写完成，长度={len(transcript)}")

        if transcript:
            try:
                from extractors.transcript_filter import is_transcript_recipe_relevant
                if not is_transcript_recipe_relevant(transcript):
                    print(f"[douyin] 转写疑似歌词/噪音，已排除")
                    transcript = ""
            except ImportError:
                pass

        if len(transcript) < 30:
            desc_has_recipe = any(
                kw in description
                for kw in ("食材", "步骤", "做法", "克", "适量", "翻炒", "调料", "配料", "焯水")
            )
            if not (len(description) >= 300 and desc_has_recipe):
                try:
                    from extractors.vision_extract import extract_recipe_from_video_frames
                    vision_text = extract_recipe_from_video_frames(
                        video_url,
                        referer="https://www.iesdouyin.com"
                    )
                    if vision_text:
                        transcript = transcript + "\n\n" + vision_text if transcript else vision_text
                        print(f"[douyin] VLM 分析完成")
                except Exception as e:
                    print(f"[douyin] VLM 分析失败: {e}")
    else:
        print("[douyin] 未找到视频直链，仅使用页面/API 文案")

    combined_parts = [f"【视频描述】\n{description}"] if description else []
    if transcript:
        combined_parts.append(f"【字幕/语音文字】\n{transcript}")
    combined = "\n\n".join(combined_parts) if combined_parts else description

    if (not description or len(description) < 10) and len(transcript) < 20:
        raise RuntimeError("未能从抖音页面提取到有效文案，请检查链接是否有效")

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "douyin",
    }
