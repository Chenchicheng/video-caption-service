"""
VLM 视频帧理解模块 - 用多模态大模型直接"看图识菜谱"
解决核心问题：背景音乐导致 ASR 转写歌词而非菜谱步骤

流程：ffmpeg 抽帧 → Base64 编码 → Qwen3-VL（SiliconFlow）→ 菜谱文本
优势：完全不依赖音频，背景音乐对结果零影响

性能优化：将帧分成多批并行请求，总耗时 ≈ 单批耗时（而非帧数 × 单帧时间）
"""

import os
import base64
import subprocess
import tempfile
import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
VLM_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
VLM_API_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 并行批次数：16 帧 → 2 批，每批 8 帧并行，总时间约减半
_NUM_BATCHES = 4

_RECIPE_VISION_PROMPT = """这是烹饪视频的截图（共{n}张，按时间顺序）。请识别画面中所有文字（字幕/贴纸/注释），并提取：
1. 菜品名称
2. 食材（含用量）
3. 烹饪步骤（按顺序）
4. 调料用量

直接输出结果，格式：
【菜品名称】
【食材】- xxx
【步骤】1. xxx
【调料】- xxx

看不到相关内容的帧可忽略。"""


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

    duration = _probe_duration(video_url, referer, user_agent)
    if duration > 0:
        fps = num_frames / duration
        print(f"[vision] 视频时长 {duration:.1f}s，按 {fps:.3f}fps 抽取 {num_frames} 帧")
    else:
        fps = 0.5
        print(f"[vision] 时长探测失败，使用默认 {fps}fps")

    # scale=640:-1 宽度限制 640px，大幅减小 base64 payload
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
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _call_vlm_single(frames: list[str], batch_idx: int = 0) -> str:
    """向 VLM 发送一批帧，返回文本"""
    if not SILICONFLOW_API_KEY:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY 环境变量")
    if not frames:
        return ""

    image_contents = []
    for path in frames:
        b64 = _encode_image_base64(path)
        image_contents.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}",
                "detail": "low",
            },
        })
    image_contents.append({
        "type": "text",
        "text": _RECIPE_VISION_PROMPT.format(n=len(frames)),
    })

    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": image_contents}],
        "max_tokens": 1000,
        "temperature": 0.1,
    }

    t0 = time.time()
    print(f"[vision] 批次{batch_idx+1}: 发送 {len(frames)} 张图...")
    resp = requests.post(
        VLM_API_URL,
        headers={
            "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=40,   # 单批最多等 40s，防止卡死
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    print(f"[vision] 批次{batch_idx+1}: 返回 {len(text)} 字，用时 {time.time() - t0:.1f}s")
    return text


def _call_vlm_parallel(frames: list[str], num_batches: int = _NUM_BATCHES) -> str:
    """
    将帧均分成 num_batches 批，并行发送给 VLM，合并返回结果。
    总耗时 ≈ 单批耗时（而非串行的 num_batches 倍）。
    """
    if not frames:
        return ""

    # 帧数较少时不必并行
    if len(frames) <= 4 or num_batches <= 1:
        return _call_vlm_single(frames)

    batch_size = (len(frames) + num_batches - 1) // num_batches
    batches = [frames[i: i + batch_size] for i in range(0, len(frames), batch_size)]
    print(f"[vision] 并行发送 {len(batches)} 批（每批 {batch_size} 帧）...")

    results: list[str] = [""] * len(batches)
    # 总超时 = 单批超时(40s) + 少量缓冲，超时后放弃未完成的批次，用已有结果返回
    total_timeout = 50
    with ThreadPoolExecutor(max_workers=len(batches)) as ex:
        future_to_idx = {
            ex.submit(_call_vlm_single, batch, idx): idx
            for idx, batch in enumerate(batches)
        }
        done, not_done = concurrent.futures.wait(
            future_to_idx.keys(), timeout=total_timeout
        )
        if not_done:
            print(f"[vision] {len(not_done)} 个批次超时未返回，使用已完成的 {len(done)} 批结果")
        for future in done:
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                print(f"[vision] 批次{idx+1} 请求失败: {e}")

    # 合并各批结果（按帧时间顺序）
    merged = "\n\n".join(r for r in results if r)
    return merged


def _is_useful_result(text: str) -> bool:
    if not text or len(text) < 20:
        return False
    markers = ["食材", "步骤", "调料", "菜品", "做法", "配料", "克", "勺", "适量"]
    return any(m in text for m in markers)


def extract_recipe_from_video_frames(
    video_url: str,
    *,
    referer: str = "https://www.xiaohongshu.com",
    num_frames: int = 16,
) -> str:
    """
    主入口：从视频 URL 抽帧 → 并行 VLM 理解 → 返回菜谱文本
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

            result = _call_vlm_parallel(frames)

            if not _is_useful_result(result):
                print(f"[vision] VLM 返回内容不含菜谱信息，忽略: {result[:60]}...")
                return ""

            print(f"[vision] 分析完成，总用时 {time.time() - t_total:.1f}s")
            return result

    except Exception as e:
        print(f"[vision] VLM 分析失败: {e}")
        return ""
