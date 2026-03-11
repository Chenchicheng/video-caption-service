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
    """从 URL 或 HTML 提取 24 位 noteId"""
    m = re.search(r"(?:xiaohongshu\.com|xhslink\.com)/explore/([a-f0-9]{24})", url, re.I)
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


def extract(url: str) -> dict:
    """
    从小红书链接提取文案（页面 meta + 内嵌 JSON）
    """
    url = url.strip()
    final_url = _resolve_url(url)

    # xhslink 短链：若拿到的是 xhslink 页，尝试二次请求 xiaohongshu.com 完整页
    html = ""
    try:
        resp = requests.get(final_url, headers=HEADERS, timeout=20, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        final_url = resp.url

        if "xhslink.com" in final_url:
            note_id = _extract_note_id(html, final_url)
            if note_id:
                explore_url = f"https://www.xiaohongshu.com/explore/{note_id}"
                resp2 = requests.get(explore_url, headers=HEADERS, timeout=20, allow_redirects=True)
                if resp2.ok:
                    html = resp2.text
                    final_url = resp2.url
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
    transcript = ""  # 小红书无公开字幕 API

    combined = description
    if transcript:
        combined = f"【视频描述】\n{description}\n\n【字幕/语音文字】\n{transcript}"

    if not description or len(description) < 10:
        raise RuntimeError("未能从小红书页面提取到有效文案，请检查链接是否有效")

    return {
        "transcript": transcript,
        "description": description,
        "combined": combined,
        "platform": "xiaohongshu",
    }
