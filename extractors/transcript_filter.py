"""
过滤 Whisper 转写中的非菜谱内容
当视频仅有背景音乐时，ASR 可能转写出歌词或噪音，应排除以免污染 AI 提取

两级过滤策略：
1. 快速关键词预检（省钱、省时）：含明确菜谱词 → 直接通过
2. LLM 智能判断（准确）：无明确词时调用 LLM 区分"歌词/闲聊"和"菜谱旁白"
"""

import os
import requests

# 高置信度菜谱关键词：出现任一则无需 LLM，直接认为是菜谱相关
_HIGH_CONFIDENCE_KEYWORDS = (
    "食材", "步骤", "做法", "菜谱", "调料", "辅料", "主料",
    "克", "毫升", "适量", "少许",
    "料酒", "酱油", "生抽", "老抽", "蚝油", "豆瓣酱",
    "翻炒", "爆炒", "大火", "小火", "中火", "焖", "炖", "蒸",
    "备用", "装盘", "出锅", "加入", "放入", "倒入",
    "腌制", "焯水", "热锅凉油",
)

# 明显歌词/非菜谱特征：出现这些则直接排除，不用 LLM
_LYRIC_SIGNALS = (
    "oh", "yeah", "baby", "la la", "哦哦", "啊啊",
    "爱你", "想你", "分手", "伤心", "泪水", "孤独",
    "副歌", "verse", "chorus",
)


def _quick_check(transcript: str) -> str:
    """
    快速预检，返回 'pass' / 'reject' / 'uncertain'
    """
    t = transcript.strip().lower()
    if not t or len(t) < 5:
        return "reject"

    for kw in _HIGH_CONFIDENCE_KEYWORDS:
        if kw in transcript:
            return "pass"

    for sig in _LYRIC_SIGNALS:
        if sig in t:
            return "reject"

    return "uncertain"


def _llm_check(transcript: str) -> bool:
    """
    调用 SiliconFlow LLM 判断转写是否为菜谱相关
    """
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        # 没有 API key，宽松策略：50字以上的文本默认保留
        return len(transcript.strip()) >= 50

    prompt = f"""请判断以下文本是否是烹饪/菜谱视频的旁白或字幕内容。

文本：
{transcript[:500]}

判断标准：
- 是菜谱旁白：包含食材说明、烹饪操作、调料用量、步骤描述等
- 不是菜谱旁白：歌词、广告词、日常闲聊、纯背景音乐转写的杂音

只需回答：是 或 否"""

    try:
        resp = requests.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "Qwen/Qwen2.5-7B-Instruct",  # 用小模型，快且便宜
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 5,
                "temperature": 0.0,
            },
            timeout=10,
        )
        resp.raise_for_status()
        answer = resp.json()["choices"][0]["message"]["content"].strip()
        return answer.startswith("是")
    except Exception as e:
        print(f"[filter] LLM 判断失败，宽松保留: {e}")
        return len(transcript.strip()) >= 50


def is_transcript_recipe_relevant(transcript: str) -> bool:
    """
    判断转写内容是否可能为菜谱相关。
    若疑似背景音乐歌词或噪音，返回 False，不应加入 combined 供 AI 提取。
    """
    if not transcript:
        return False

    result = _quick_check(transcript)

    if result == "pass":
        return True
    if result == "reject":
        return False

    # uncertain：走 LLM 精确判断
    print(f"[filter] 关键词无法判断，调用 LLM 判断转写相关性...")
    return _llm_check(transcript)
