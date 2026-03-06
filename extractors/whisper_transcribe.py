"""
Whisper 语音转文字
用于没有字幕的视频，先用 yt-dlp 下载音频，再用 faster-whisper 转文字
"""

import os
import tempfile
import yt_dlp
from faster_whisper import WhisperModel

# 模型单例，避免重复加载（tiny 约 70MB，适合 CPU 服务器）
_model = None


def _get_model(model_size: str = "tiny") -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model


def download_audio(video_url: str, output_path: str) -> bool:
    """使用 yt-dlp 下载视频音频到 mp3，返回是否成功"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com",
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",  # 低码率，减小文件大小
        }],
        # 限制最长 10 分钟，避免超大文件
        "match_filter": yt_dlp.utils.match_filter_func("duration < 600"),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        return True
    except Exception as e:
        print(f"[whisper] 音频下载失败: {e}")
        return False


def transcribe_audio(audio_path: str, language: str = "zh") -> str:
    """对音频文件进行语音转文字，返回文本"""
    model = _get_model("tiny")
    segments, _ = model.transcribe(
        audio_path,
        language=language,
        beam_size=3,
        vad_filter=True,  # 过滤静音段
    )
    texts = [seg.text.strip() for seg in segments if seg.text.strip()]
    return " ".join(texts)


def transcribe_from_url(video_url: str, language: str = "zh") -> str:
    """
    从视频 URL 下载音频并转文字
    返回转写文本，失败返回空字符串
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_base = os.path.join(tmpdir, "audio")
        audio_file = audio_base + ".mp3"

        success = download_audio(video_url, audio_base)
        if not success or not os.path.exists(audio_file):
            return ""

        return transcribe_audio(audio_file, language=language)
