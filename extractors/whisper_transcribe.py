"""
Whisper 语音转文字
用于没有字幕的视频，先获取音频流，再用 faster-whisper 转文字
"""

import os
import tempfile
import requests
from faster_whisper import WhisperModel

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 模型单例，避免重复加载（tiny 约 70MB，适合 CPU 服务器）
_model = None


def _get_model(model_size: str = "tiny") -> WhisperModel:
    global _model
    if _model is None:
        print(f"[whisper] 加载模型 {model_size}...")
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        print("[whisper] 模型加载完成")
    return _model


def download_audio_bilibili(bvid: str, cid: int, output_path: str) -> bool:
    """
    通过 Bilibili 播放 API 直接获取音频流 URL 并下载
    不经过 yt-dlp 网页抓取，避免 412 错误
    """
    # 获取播放地址（不需要登录，qn=16 为 360p，只需要音频）
    api = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16&fnver=0&fourk=0"
    print(f"[whisper] 请求播放 API: {api}")

    try:
        resp = requests.get(api, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        print(f"[whisper] 播放 API 返回 code={data.get('code')}")

        if data.get("code") != 0:
            print(f"[whisper] 播放 API 错误: {data.get('message')}")
            return False

        play_data = data.get("data", {})

        # fnval=16 返回 dash 格式，取音频流
        dash = play_data.get("dash")
        audio_url = None
        if dash:
            audio_list = dash.get("audio", [])
            if audio_list:
                # 取第一个（最高质量）
                audio_url = audio_list[0].get("baseUrl") or audio_list[0].get("base_url")
                print(f"[whisper] 找到 dash 音频流: {audio_url[:80] if audio_url else None}...")

        # 兜底：durl 格式（mp4）
        if not audio_url:
            durl = play_data.get("durl", [])
            if durl:
                audio_url = durl[0].get("url")
                print(f"[whisper] 使用 durl 流: {audio_url[:80] if audio_url else None}...")

        if not audio_url:
            print("[whisper] 未找到可用音频流")
            return False

        # 下载音频
        print(f"[whisper] 开始下载音频...")
        audio_resp = requests.get(audio_url, headers=HEADERS, timeout=60, stream=True)
        audio_resp.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in audio_resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"[whisper] 音频下载完成，大小: {size_mb:.1f} MB")
        return True

    except Exception as e:
        print(f"[whisper] 音频下载失败: {e}")
        return False


def transcribe_audio(audio_path: str, language: str = "zh") -> str:
    """对音频文件进行语音转文字，返回文本"""
    import time
    model = _get_model("tiny")
    print(f"[whisper] 开始转写，文件: {audio_path}")
    t0 = time.time()
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=3,
        vad_filter=True,
    )
    texts = [seg.text.strip() for seg in segments if seg.text.strip()]
    result = " ".join(texts)
    elapsed = time.time() - t0
    print(f"[whisper] 转写完成，字数: {len(result)}，用时: {elapsed:.1f}s")
    return result


def transcribe_bilibili(bvid: str, cid: int, language: str = "zh") -> str:
    """
    专用于 Bilibili 的转写入口：通过官方 API 下载音频 + Whisper 转写
    """
    import time
    t_total = time.time()
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_file = os.path.join(tmpdir, "audio.m4s")

        t0 = time.time()
        success = download_audio_bilibili(bvid, cid, audio_file)
        print(f"[whisper] 下载用时: {time.time() - t0:.1f}s")

        if not success or not os.path.exists(audio_file):
            print("[whisper] 音频文件不存在，放弃转写")
            return ""

        result = transcribe_audio(audio_file, language=language)
        print(f"[whisper] 总用时: {time.time() - t_total:.1f}s")
        return result
