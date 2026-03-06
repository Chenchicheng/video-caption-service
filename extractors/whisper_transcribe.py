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
        "quiet": False,   # 开启详细输出
        "no_warnings": False,
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com",
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "64",
        }],
        "match_filter": yt_dlp.utils.match_filter_func("duration < 600"),
    }
    try:
        print(f"[whisper] 开始下载音频: {video_url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        print(f"[whisper] 音频下载完成，目标路径: {output_path}.mp3")
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

        # yt-dlp 有时会加上额外后缀，列出目录看实际文件名
        files = os.listdir(tmpdir)
        print(f"[whisper] tmpdir 文件列表: {files}")

        if not success:
            print("[whisper] 下载失败，退出")
            return ""

        if not os.path.exists(audio_file):
            # 尝试找到任意音频文件
            audio_candidates = [os.path.join(tmpdir, f) for f in files if f.endswith((".mp3", ".m4a", ".webm", ".opus"))]
            print(f"[whisper] audio.mp3 不存在，候选文件: {audio_candidates}")
            if not audio_candidates:
                return ""
            audio_file = audio_candidates[0]

        print(f"[whisper] 开始 Whisper 转写: {audio_file}")
        return transcribe_audio(audio_file, language=language)
