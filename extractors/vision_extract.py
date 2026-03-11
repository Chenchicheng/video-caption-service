"""
VLM 视频帧理解模块 - 用多模态大模型直接"看图识菜谱"
解决核心问题：背景音乐导致 ASR 转写歌词而非菜谱步骤

流程：ffmpeg 抽帧 → Base64 编码 → Qwen2.5-VL（SiliconFlow）→ 菜谱文本
优势：完全不依赖音频，背景音乐对结果零影响
"""

import os
import base64
import subprocess
import tempfile
import time
from pathlib import Path

import requests

SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
VLM_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
VLM_API_URL = "https://api.siliconflow.cn/v1/chat/completions"

_RECIPE_VISION_PROMPT = """这是一个烹饪/菜谱视频的截图序列（共{n}张），请仔细分析每张图片后，提取以下信息：

1. 菜品名称（从画面或字幕中读取）
2. 食材列表（含用量，如：鸡蛋 2个、盐 适量）
3. 烹饪步骤（按顺序，从画面/字幕/文字中读取）
4. 调料和用量

注意：
- 请读取视频中出现的所有文字（字幕、贴纸、注释等）
- 如果看到食材配料表画面，完整抄录
- 如果步骤有数字序号，按序号排列
- 如果某张图片模糊或无关，忽略即可

请用中文输出，格式：
【菜品名称】xxx
【食材】
- xxx
【步骤】
1. xxx
【调料】
- xxx"""


def _probe_duration(
    video_url: str,
    referer: str,
    user_agent: str,
) -> float:
    """用 ffprobe 探测视频时长（秒），失败返回 0"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        "-headers", f"Referer: {referer}\r\nUser-Agent: {user_agent}\r\n",
        video_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _extract_frames(
    video_url: str,
    out_dir: str,
    *,
    referer: str = "https://www.xiaohongshu.com",
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    num_frames: int = 16,
) -> list[str]:
    """
    ffmpeg 从视频 URL 均匀抽帧，返回图片路径列表。
    先探测视频时长，再动态计算 fps，保证 num_frames 帧均匀覆盖整段视频。
    """
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%04d.jpg")

    # 探测时长，动态计算 fps 使帧均匀分布在整段视频
    duration = _probe_duration(video_url, referer, user_agent)
    if duration > 0:
        fps = num_frames / duration
        print(f"[vision] 视频时长 {duration:.1f}s，按 {fps:.3f}fps 抽取 {num_frames} 帧")
    else:
        fps = 0.5  # 探测失败时兜底：每 2 秒一帧
        print(f"[vision] 时长探测失败，使用默认 {fps}fps")

    # scale=640:-1 将宽度限制在 640px（高度等比缩放），大幅减小 base64 payload
    cmd = [
        "ffmpeg", "-y",
        "-referer", referer,
        "-user_agent", user_agent,
        "-i", video_url,
        "-vf", f"fps={fps:.6f},scale=640:-1",
        "-frames:v", str(num_frames),
        "-q:v", "5",
        "-loglevel", "error",
        pattern,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[vision] ffmpeg 抽帧失败: {e}")
        return []

    frames = sorted(
        [os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith(".jpg")]
    )
    print(f"[vision] 共抽取 {len(frames)} 帧")
    return frames


def _encode_image_base64(path: str) -> str:
    """将图片文件编码为 base64 字符串"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _call_vlm(frames: list[str]) -> str:
    """
    将多张图片发送给 Qwen2.5-VL，让模型直接理解视频内容并提取菜谱
    """
    if not SILICONFLOW_API_KEY:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY 环境变量")

    if not frames:
        return ""

    # 构建多图消息：文字 prompt + 多张图片
    image_contents = []
    for path in frames:
        b64 = _encode_image_base64(path)
        image_contents.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}",
                "detail": "low",   # low 节省 token，对菜谱文字已足够
            },
        })

    image_contents.append({
        "type": "text",
        "text": _RECIPE_VISION_PROMPT.format(n=len(frames)),
    })

    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": image_contents}],
        "max_tokens": 1500,
        "temperature": 0.1,
    }

    t0 = time.time()
    print(f"[vision] 发送 {len(frames)} 张图给 {VLM_MODEL}...")
    resp = requests.post(
        VLM_API_URL,
        headers={
            "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    result = resp.json()
    text = result["choices"][0]["message"]["content"].strip()
    print(f"[vision] VLM 返回 {len(text)} 字，用时 {time.time() - t0:.1f}s")
    return text


def _is_useful_result(text: str) -> bool:
    """判断 VLM 返回内容是否包含有效菜谱信息"""
    if not text or len(text) < 20:
        return False
    # 有任一结构性标记则认为有效
    markers = ["食材", "步骤", "调料", "菜品", "做法", "配料", "克", "勺", "适量"]
    return any(m in text for m in markers)


def extract_recipe_from_video_frames(
    video_url: str,
    *,
    referer: str = "https://www.xiaohongshu.com",
    num_frames: int = 16,
) -> str:
    """
    主入口：从视频 URL 抽帧 → VLM 理解 → 返回菜谱文本
    失败时返回空字符串，不抛异常（由调用方决定如何降级）
    """
    t_total = time.time()
    print(f"[vision] 开始视频帧 VLM 分析: {video_url[:60]}...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            frames = _extract_frames(
                video_url, tmpdir, referer=referer, num_frames=num_frames
            )
            if not frames:
                print("[vision] 抽帧失败，跳过 VLM 分析")
                return ""

            result = _call_vlm(frames)

            if not _is_useful_result(result):
                print(f"[vision] VLM 返回内容不含菜谱信息，忽略: {result[:60]}...")
                return ""

            print(f"[vision] 分析完成，总用时 {time.time() - t_total:.1f}s")
            return result

    except Exception as e:
        print(f"[vision] VLM 分析失败: {e}")
        return ""
