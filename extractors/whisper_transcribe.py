"""
语音转文字
优先使用 SiliconFlow 云端 API（快，3-5秒），
未配置 SILICONFLOW_API_KEY 时降级为本地 faster-whisper（慢，CPU 约 60-120 秒）
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


def transcribe_with_siliconflow(audio_path: str) -> str:
    """使用 SiliconFlow 云端 API 转写（快，3-5秒）"""
    import time
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY")

    print("[asr] 使用 SiliconFlow API 转写...")
    t0 = time.time()
    with open(audio_path, "rb") as f:
        resp = requests.post(
            "https://api.siliconflow.cn/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
            data={"model": "FunAudioLLM/SenseVoiceSmall"},
            timeout=60,
        )
    resp.raise_for_status()
    result = resp.json().get("text", "").strip()
    print(f"[asr] SiliconFlow 转写完成，字数: {len(result)}，用时: {time.time() - t0:.1f}s")
    return result


def transcribe_audio_local(audio_path: str, language: str = "zh") -> str:
    """本地 faster-whisper 转写（慢，CPU 约 60-120 秒）"""
    import time
    model = _get_model("tiny")
    print(f"[asr] 本地 Whisper 开始转写: {audio_path}")
    t0 = time.time()
    segments, _ = model.transcribe(
        audio_path,
        language=language,
        beam_size=3,
        vad_filter=True,
    )
    texts = [seg.text.strip() for seg in segments if seg.text.strip()]
    result = " ".join(texts)
    print(f"[asr] 本地 Whisper 转写完成，字数: {len(result)}，用时: {time.time() - t0:.1f}s")
    return result


def transcribe_audio(audio_path: str, language: str = "zh") -> str:
    """自动选择转写方式：优先 SiliconFlow，降级本地 Whisper"""
    try:
        return transcribe_with_siliconflow(audio_path)
    except RuntimeError:
        print("[asr] 未配置 SILICONFLOW_API_KEY，使用本地 Whisper（较慢）")
        return transcribe_audio_local(audio_path, language)
    except Exception as e:
        print(f"[asr] SiliconFlow 失败: {e}，降级本地 Whisper")
        return transcribe_audio_local(audio_path, language)


def transcribe_bilibili(bvid: str, cid: int, language: str = "zh") -> str:
    """专用于 Bilibili：官方 API 下载音频 + 转写"""
    import time
    t_total = time.time()
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_file = os.path.join(tmpdir, "audio.m4s")

        t0 = time.time()
        success = download_audio_bilibili(bvid, cid, audio_file)
        print(f"[asr] 下载用时: {time.time() - t0:.1f}s")

        if not success or not os.path.exists(audio_file):
            print("[asr] 音频文件不存在，放弃转写")
            return ""

        result = transcribe_audio(audio_file, language=language)
        print(f"[asr] 总用时: {time.time() - t_total:.1f}s")
        return result
