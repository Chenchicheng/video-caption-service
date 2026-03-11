"""
小红书视频/笔记文案提取
- description: 页面 meta（og:title、og:description）+ 内嵌 JSON（desc、title、content）
- transcript: 无公开字幕 API，留空
- xhslink 短链会跟随重定向到 xiaohongshu.com/explore/xxx
"""

import re
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.xiaohongshu.com/",
}


def _decode_unicode_escape(s: str) -> str:
    """解码 JSON 中的 \\uXXXX"""
    def replace(m):
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return m.group(0)
    return re.sub(r"\\u([0-9a-fA-F]{4})", replace, s)


def _resolve_url(url: str) -> str:
    """跟随重定向获取最终 URL（xhslink 短链）"""
    resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    return resp.url or url


def _extract_note_id(html: str, url: str) -> str | None:
    """从 URL 或 HTML 提取 24 位 noteId（支持 explore、discovery/item 等格式）"""
    patterns = [
        r"(?:xiaohongshu\.com|xhslink\.com)/explore/([a-f0-9]{24})",
        r"/discovery/item/([a-f0-9]{24})",
        r"/explore/([a-f0-9]{24})",
    ]
    for pat in patterns:
        m = re.search(pat, url, re.I)
        if m:
            return m.group(1)
    m = re.search(r'"noteId"\s*:\s*"([a-f0-9]{24})"', html)
    if m:
        return m.group(1)
    m = re.search(r"/explore/([a-f0-9]{24})", html)
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


def _decode_json_str(s: str) -> str:
    """解码 JSON 中转义的字符串"""
    return _decode_unicode_escape(s).replace("\\/", "/").replace("\\n", "\n")


def _extract_video_url(html: str) -> str | None:
    """从小红书页面提取视频直链（og:video、originVideoKey、xhscdn、video 等）"""
    # 1. og:video meta（兼容 name 在前、content 在前等多种写法）
    for pat in [
        r'<meta\s+name=["\']og:video["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+\.mp4[^"\']*)["\']\s+name=["\']og:video["\']',
        r'<meta\s+(?:property|name)=["\']og:video["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+content=["\']([^"\']+\.mp4[^"\']*)["\']\s+(?:property|name)=["\']og:video["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            url = m.group(1).strip()
            if ("xhscdn" in url or "sns-video" in url) and ".mp4" in url:
                return url
    # 2. originVideoKey
    m = re.search(r'"originVideoKey"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        key = _decode_json_str(m.group(1))
        if len(key) > 5:
            return f"https://sns-video-bd.xhscdn.com/{key}"
    # 3. xhscdn 直链（含转义 \/ 等形式）
    m = re.search(r'https?:\\?/\\?/sns-video-(?:hw|bd)\.xhscdn\.com[^\s"\']+\.mp4', html)
    if m:
        return _decode_json_str(m.group(0))
    m = re.search(r'https://sns-video-(?:hw|bd)\.xhscdn\.com/[^\s"\'<>]+\.mp4[^\s"\'<>]*', html)
    if m:
        return m.group(0).rstrip('"\'>&')
    # 4. masterUrl / video_url / videoUrl
    m = re.search(r'"masterUrl"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        url = _decode_json_str(m.group(1))
        if "xhscdn" in url and ".mp4" in url:
            return url
    m = re.search(r'"video_url"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        url = _decode_json_str(m.group(1))
        if "xhscdn" in url:
            return url
    m = re.search(r'"videoUrl"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
    if m:
        url = _decode_json_str(m.group(1))
        if "xhscdn" in url:
            return url
    # 5. 任意 xhscdn mp4（兜底）
    m = re.search(r'(https://sns-video-[a-z0-9-]+\.xhscdn\.com/[a-zA-Z0-9/_\-\.]+\.mp4[^"\'<>\s]*)', html)
    if m:
        return m.group(1).rstrip('"\'>&')
    return None


def _extract_inline_json(html: str) -> list[str]:
    """从小红书内嵌 JSON 提取 desc、title、content"""
    found = []
    patterns = [
        r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"',
        r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"',
        r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html):
            s = _decode_unicode_escape(m.group(1)).replace("\\n", "\n")
            s = re.sub(r"<[^>]+>", " ", s).strip()
            if len(s) > 8 and s not in found and s not in ("null", "undefined", "{}", "[]"):
                found.append(s)
    return found


def extract_with_video_url(url: str, video_url: str) -> dict:
    """
    客户端已提供视频直链，直接走 Whisper，仍抓取页面获取 description
    """
    print(f"[xiaohongshu] 使用客户端传入的视频直链: {video_url[:60]}...")
    url = url.strip()
    final_url = _resolve_url(url)
    html = ""
    try:
        resp = requests.get(final_url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        final_url = resp.url
        note_id = _extract_note_id(html, final_url)
        if note_id and ("xhslink.com" in final_url or "/discovery/item/" in final_url):
            explore_url = f"https://www.xiaohongshu.com/explore/{note_id}"
            resp2 = requests.get(explore_url, headers=HEADERS, timeout=20, allow_redirects=True)
            if resp2.ok:
                html = resp2.text
    except requests.RequestException:
        html = ""

    meta_parts = _extract_meta_content(html)
    inline_parts = _extract_inline_json(html)
    desc_lines = []
    if meta_parts:
        desc_lines.append("【页面摘要】\n" + "\n".join(meta_parts))
    if inline_parts:
        desc_lines.append("【笔记详情】\n" + "\n".join(inline_parts))
    description = "\n\n".join(desc_lines).strip() or "（视频内容）"

    transcript = ""
    try:
        from extractors.whisper_transcribe import transcribe_xiaohongshu
        transcript = transcribe_xiaohongshu(video_url)
        print(f"[xiaohongshu] Whisper 转写完成，长度={len(transcript)}")
    except Exception as e:
        print(f"[xiaohongshu] Whisper 转写失败: {e}")

    # 无语音/纯文案视频：OCR 画面文字
    if len(transcript) < 30:
        try:
            from extractors.ocr_video import extract_text_from_video
            ocr_text = extract_text_from_video(video_url)
            if ocr_text and len(ocr_text) >= 10:
                transcript = ocr_text if not transcript else f"{transcript}\n\n{ocr_text}"
                print(f"[xiaohongshu] OCR 画面文字完成，长度={len(ocr_text)}")
        except Exception as e:
            print(f"[xiaohongshu] OCR 失败: {e}")

    combined = description
    try:
        from extractors.transcript_filter import is_transcript_recipe_relevant
        if transcript and is_transcript_recipe_relevant(transcript):
            combined = f"【视频描述】\n{description}\n\n【字幕/语音文字】\n{transcript}"
        elif transcript and not is_transcript_recipe_relevant(transcript):
            print(f"[xiaohongshu] 转写疑似歌词/噪音，已排除: {transcript[:40]}...")
    except ImportError:
        if transcript:
            combined = f"【视频描述】\n{description}\n\n【字幕/语音文字】\n{transcript}"

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "xiaohongshu",
    }


def extract(url: str) -> dict:
    """
    从小红书链接提取文案（页面 meta + 内嵌 JSON）
    若有视频直链则走 Whisper 音频转写（与 B 站一致）
    """
    url = url.strip()
    final_url = _resolve_url(url)
    print(f"[xiaohongshu] 请求 URL: {url[:80]}...")
    print(f"[xiaohongshu] 重定向后: {final_url[:80]}...")

    # xhslink 短链 或 discovery/item：尝试二次请求 explore 完整页
    html = ""
    try:
        resp = requests.get(final_url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        final_url = resp.url

        note_id = _extract_note_id(html, final_url)
        if note_id and ("xhslink.com" in final_url or "/discovery/item/" in final_url):
            explore_url = f"https://www.xiaohongshu.com/explore/{note_id}"
            print(f"[xiaohongshu] 二次请求 explore 页: {explore_url}")
            resp2 = requests.get(explore_url, headers=HEADERS, timeout=20, allow_redirects=True)
            if resp2.ok:
                html = resp2.text
                final_url = resp2.url
            # 若仍无视频信息，尝试移动端 UA（部分站点对移动端返回更完整数据）
            if not _extract_video_url(html):
                mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
                resp3 = requests.get(
                    explore_url,
                    headers={**HEADERS, "User-Agent": mobile_ua},
                    timeout=20,
                    allow_redirects=True,
                )
                if resp3.ok and len(resp3.text) > len(html):
                    html = resp3.text
                    print(f"[xiaohongshu] 使用移动端 UA 重试，html 长度: {len(html)}")
    except requests.RequestException as e:
        raise RuntimeError(f"请求小红书页面失败: {e}")

    meta_parts = _extract_meta_content(html)
    inline_parts = _extract_inline_json(html)

    desc_lines = []
    if meta_parts:
        desc_lines.append("【页面摘要】\n" + "\n".join(meta_parts))
    if inline_parts:
        desc_lines.append("【笔记详情】\n" + "\n".join(inline_parts))

    description = "\n\n".join(desc_lines).strip()
    transcript = ""

    # 尝试提取视频直链，走 Whisper 音频转写（与 B 站一致）
    video_url = _extract_video_url(html)
    if not video_url:
        # 诊断：页面可能被反爬或为 SPA 需 JS 渲染
        has_og = "og:video" in html.lower()
        has_key = "originVideoKey" in html
        has_xhscdn = "xhscdn" in html and ".mp4" in html
        print(f"[xiaohongshu] 视频直链未找到，诊断: og:video={has_og} originVideoKey={has_key} xhscdn={has_xhscdn} html_len={len(html)}")
    if video_url:
        print(f"[xiaohongshu] 找到视频直链: {video_url[:60]}...")
        try:
            from extractors.whisper_transcribe import transcribe_xiaohongshu
            transcript = transcribe_xiaohongshu(video_url)
            print(f"[xiaohongshu] Whisper 转写完成，长度={len(transcript)}")
        except Exception as e:
            print(f"[xiaohongshu] Whisper 转写失败: {e}")
        # 无语音/纯文案视频：OCR 画面文字
        if len(transcript) < 30:
            try:
                from extractors.ocr_video import extract_text_from_video
                ocr_text = extract_text_from_video(video_url)
                if ocr_text and len(ocr_text) >= 10:
                    transcript = ocr_text if not transcript else f"{transcript}\n\n{ocr_text}"
                    print(f"[xiaohongshu] OCR 画面文字完成，长度={len(ocr_text)}")
            except Exception as e:
                print(f"[xiaohongshu] OCR 失败: {e}")
    else:
        print("[xiaohongshu] 未找到视频直链，仅使用页面文案")

    combined = description
    try:
        from extractors.transcript_filter import is_transcript_recipe_relevant
        if transcript and is_transcript_recipe_relevant(transcript):
            combined = f"【视频描述】\n{description}\n\n【字幕/语音文字】\n{transcript}"
        elif transcript and not is_transcript_recipe_relevant(transcript):
            print(f"[xiaohongshu] 转写疑似歌词/噪音，已排除: {transcript[:40]}...")
    except ImportError:
        if transcript:
            combined = f"【视频描述】\n{description}\n\n【字幕/语音文字】\n{transcript}"

    if (not description or len(description) < 10) and len(transcript) < 20:
        raise RuntimeError("未能从小红书页面提取到有效文案，请检查链接是否有效")

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "xiaohongshu",
    }
