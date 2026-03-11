"""
视频画面 OCR - 从无语音/纯文案视频中提取文字
用于：只有画面文字、无旁白的视频（如菜谱步骤、配料表）
"""

import os
from typing import Any

_ocr_reader: Any = None
import subprocess
import tempfile
import time


def _extract_frames(video_url: str, out_dir: str, referer: str, user_agent: str) -> list[str]:
    """
    ffmpeg 从 URL 抽帧，每秒 0.5 帧（每 2 秒一帧），最多 30 帧
    """
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%04d.png")
    cmd = [
        "ffmpeg", "-y",
        "-referer", referer,
        "-user_agent", user_agent,
        "-i", video_url,
        "-vf", "fps=0.5",  # 每 2 秒一帧
        "-frames:v", "30",  # 最多 30 帧（约 1 分钟视频）
        "-loglevel", "error",
        pattern,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=90)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[ocr] ffmpeg 抽帧失败: {e}")
        return []

    frames = []
    for f in sorted(os.listdir(out_dir)):
        if f.endswith(".png"):
            frames.append(os.path.join(out_dir, f))
    return frames


def _similar_text(a: str, b: str) -> bool:
    """简单相似度：去除空格后是否包含或相同"""
    x = "".join(a.split()).strip()
    y = "".join(b.split()).strip()
    if not x or not y:
        return False
    return x == y or x in y or y in x


def extract_text_from_video(
    video_url: str,
    *,
    referer: str = "https://www.xiaohongshu.com",
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
) -> str:
    """
    从视频 URL 抽帧并 OCR 识别画面文字
    返回：去重合并后的文本，无文字则返回空串
    """
    try:
        import easyocr
    except ImportError:
        print("[ocr] 未安装 easyocr，请 pip install easyocr")
        return ""

    t0 = time.time()
    print(f"[ocr] 开始视频 OCR: {video_url[:50]}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        frames = _extract_frames(video_url, tmpdir, referer, user_agent)
        if not frames:
            return ""

        global _ocr_reader
        if _ocr_reader is None:
            print("[ocr] 加载 EasyOCR 模型...")
            _ocr_reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
        reader = _ocr_reader
        all_lines: list[str] = []
        seen: list[str] = []

        for path in frames:
            try:
                results = reader.readtext(path)
                for (_, text, _) in results:
                    s = text.strip()
                    if len(s) < 2:
                        continue
                    if any(_similar_text(s, x) for x in seen):
                        continue
                    seen.append(s)
                    all_lines.append(s)
            except Exception as e:
                print(f"[ocr] 帧识别异常: {e}")

    result = "\n".join(all_lines).strip()
    print(f"[ocr] OCR 完成，识别 {len(all_lines)} 条，字数 {len(result)}，用时 {time.time() - t0:.1f}s")
    return result
