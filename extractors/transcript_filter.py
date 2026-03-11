"""
过滤 Whisper 转写中的非菜谱内容
当视频仅有背景音乐时，Whisper 可能转写出歌词或噪音，应排除以免污染 AI 提取
"""

# 菜谱相关关键词，出现任一则视为可能有效
_RECIPE_KEYWORDS = (
    "食材", "步骤", "克", "适量", "少许", "盐", "糖", "油", "酱油", "料酒",
    "葱", "姜", "蒜", "炒", "煮", "煎", "蒸", "烤", "炸", "炖", "切", "备用",
    "分钟", "小时", "做法", "菜谱", "调料", "辅料", "主料", "鸡蛋", "面粉",
    "水", "搅拌", "加入", "放入", "大火", "小火", "翻炒", "装盘",
)


def is_transcript_recipe_relevant(transcript: str) -> bool:
    """
    判断转写内容是否可能为菜谱相关。
    若疑似背景音乐歌词或噪音，返回 False，不应加入 combined 供 AI 提取。
    """
    if not transcript:
        return False
    t = transcript.strip()
    # 含任一词则视为可能有效（即使较短也保留）
    for kw in _RECIPE_KEYWORDS:
        if kw in t:
            return True
    # 无任何菜谱关键词时：过短视为噪音，较长视为疑似歌词
    if len(t) < 20:
        return False
    # 20+ 字且无菜谱词 → 疑似歌词/背景音转写
    return False
