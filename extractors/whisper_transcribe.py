"""
语音转文字 - 使用 SiliconFlow 云端 API（FunAudioLLM/SenseVoiceSmall）
需要设置环境变量 SILICONFLOW_API_KEY
Bilibili: 直接下载音频流
小红书: 下载 mp4 后用 ffmpeg 提取音频
"""

import os
import subprocess
import time
import tempfile
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


def download_audio_bilibili(bvid: str, cid: int, output_path: str) -> bool:
    """通过 Bilibili 播放 API 直接下载音频流"""
    api = f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16&fnver=0&fourk=0"
    print(f"[asr] 请求播放 API: {api}")

    try:
        resp = requests.get(api, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        print(f"[asr] 播放 API 返回 code={data.get('code')}")

        if data.get("code") != 0:
            print(f"[asr] 播放 API 错误: {data.get('message')}")
            return False

        play_data = data.get("data", {})
        audio_url = None

        # dash 格式取音频流
        dash = play_data.get("dash")
        if dash:
            audio_list = dash.get("audio", [])
            if audio_list:
                audio_url = audio_list[0].get("baseUrl") or audio_list[0].get("base_url")
                print(f"[asr] 找到 dash 音频流: {str(audio_url)[:80]}...")

        # 兜底 durl 格式
        if not audio_url:
            durl = play_data.get("durl", [])
            if durl:
                audio_url = durl[0].get("url")
                print(f"[asr] 使用 durl 流: {str(audio_url)[:80]}...")

        if not audio_url:
            print("[asr] 未找到可用音频流")
            return False

        print("[asr] 开始下载音频...")
        t0 = time.time()
        audio_resp = requests.get(audio_url, headers=HEADERS, timeout=60, stream=True)
        audio_resp.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in audio_resp.iter_content(chunk_size=8192):
                f.write(chunk)

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"[asr] 音频下载完成，大小: {size_mb:.1f} MB，用时: {time.time() - t0:.1f}s")
        return True

    except Exception as e:
        print(f"[asr] 音频下载失败: {e}")
        return False


def transcribe_with_siliconflow(audio_path: str) -> str:
    """调用 SiliconFlow API 转写音频"""
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY 环境变量")

    print("[asr] 调用 SiliconFlow API 转写...")
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
    print(f"[asr] 转写完成，字数: {len(result)}，用时: {time.time() - t0:.1f}s")
    return result


def transcribe_bilibili(bvid: str, cid: int) -> str:
    """Bilibili 专用：下载音频 + SiliconFlow 转写"""
    t_total = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_file = os.path.join(tmpdir, "audio.m4s")

        success = download_audio_bilibili(bvid, cid, audio_file)
        if not success or not os.path.exists(audio_file):
            print("[asr] 音频文件不存在，放弃转写")
            return ""

        result = transcribe_with_siliconflow(audio_file)
        print(f"[asr] 总用时: {time.time() - t_total:.1f}s")
        return result


def download_video_xiaohongshu(video_url: str, output_path: str) -> bool:
    """下载小红书视频（xhscdn mp4，兜底用）"""
    xhs_headers = {
        **HEADERS,
        "Referer": "https://www.xiaohongshu.com",
    }
    try:
        print(f"[asr] 下载小红书视频: {video_url[:60]}...")
        t0 = time.time()
        resp = requests.get(video_url, headers=xhs_headers, timeout=60, stream=True)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):  # 64KB 减少 syscall
                f.write(chunk)
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"[asr] 视频下载完成，大小: {size_mb:.1f} MB，用时: {time.time() - t0:.1f}s")
        return True
    except Exception as e:
        print(f"[asr] 视频下载失败: {e}")
        return False


def transcribe_xiaohongshu(video_url: str) -> str:
    """
    小红书专用：FFmpeg 直读 URL 提取音频 -> SiliconFlow 转写
    优先用 ffmpeg -i URL，省去先下载再提取的步骤；失败时回退到下载+ffmpeg
    """
    t_total = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_file = os.path.join(tmpdir, "audio.mp3")

        # ASR 优化：16kHz 单声道 64kbps，语音转写足够，体积小上传快
        asr_args = ["-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k"]

        # 方案 1：ffmpeg 直读 URL（不落盘视频，省 I/O）
        try:
            print(f"[asr] 使用 ffmpeg 直读 URL 提取音频: {video_url[:50]}...")
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-referer", "https://www.xiaohongshu.com",
                    "-user_agent", HEADERS["User-Agent"],
                    "-i", video_url,
                    *asr_args,
                    "-loglevel", "error",
                    audio_file,
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            print(f"[asr] ffmpeg 直读失败，回退到下载+提取: {e}")

            # 方案 2：先下载视频再提取
            video_file = os.path.join(tmpdir, "video.mp4")
            if not download_video_xiaohongshu(video_url, video_file):
                return ""
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_file, *asr_args, audio_file],
                    check=True,
                    capture_output=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as e2:
                print(f"[asr] ffmpeg 提取音频失败: {e2}")
                return ""

        if not os.path.exists(audio_file) or os.path.getsize(audio_file) < 100:
            return ""

        size_kb = os.path.getsize(audio_file) / 1024
        print(f"[asr] 音频准备完成，{size_kb:.0f} KB → 转写中...")

        result = transcribe_with_siliconflow(audio_file)
        print(f"[asr] 总用时: {time.time() - t_total:.1f}s")
        return result
