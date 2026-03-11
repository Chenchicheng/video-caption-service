"""
VLM 视频帧理解模块 - 用多模态大模型直接"看图识菜谱"
解决核心问题：背景音乐导致 ASR 转写歌词而非菜谱步骤

流程：ffmpeg 抽帧 → 拼成 2×2 网格图 → 单次 VLM 请求 → 菜谱文本
优势：
- 完全不依赖音频，背景音乐对结果零影响
- 网格拼接：16帧→4张图，VLM 处理图数减少 4 倍，速度提升 3-4x
- 单次请求：保持完整上下文，不会因为分批丢失步骤
"""

import os
import base64
import subprocess
import tempfile
import time

import requests

SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
VLM_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
VLM_API_URL = "https://api.siliconflow.cn/v1/chat/completions"

_RECIPE_VISION_PROMPT = """以下是烹饪视频的截图，每张图是一个 2×2 网格，包含 4 个连续帧（左上→右上→左下→右下），按时间顺序排列。

请仔细查看每个帧中的画面和文字（字幕、贴纸、配料表、步骤注释等），提取完整菜谱：

1. 菜品名称
2. 食材清单（含用量，如：鸡蛋 2个、盐 适量）
3. 完整的烹饪步骤（按时间顺序，不要遗漏）
4. 调料清单（含用量）

注意：
- 画面中出现的所有食材/调料文字都要抄录
- 步骤要完整，能看到多少步就写多少步
- 如果帧内容重复或无关（如片头/片尾），跳过即可

输出格式：
【菜品名称】xxx
【食材】
- 食材名 用量
【步骤】
1. 步骤描述
【调料】
- 调料名 用量"""


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
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    num_frames: int = 16,
) -> list[str]:
    """
    ffmpeg 均匀抽帧。先探测时长动态计算 fps，保证帧覆盖整段视频。
    输出 480px 宽的 JPEG（为网格拼接做准备，单帧不必太大）。
    """
    os.makedirs(out_dir, exist_ok=True)
    pattern = os.path.join(out_dir, "frame_%04d.jpg")

    duration = _probe_duration(video_url, referer, user_agent)
    if duration > 0:
        fps = num_frames / duration
        print(f"[vision] 视频时长 {duration:.1f}s，抽取 {num_frames} 帧")
    else:
        fps = 0.5
        print(f"[vision] 时长探测失败，使用 {fps}fps")

    cmd = [
        "ffmpeg", "-y",
        "-referer", referer,
        "-user_agent", user_agent,
        "-i", video_url,
        "-vf", f"fps={fps:.6f},scale=480:-1",
        "-frames:v", str(num_frames),
        "-q:v", "4",
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
    print(f"[vision] 抽取 {len(frames)} 帧")
    return frames


def _create_grids(frames: list[str], out_dir: str, cols: int = 2, rows: int = 2) -> list[str]:
    """
    将帧列表拼成 cols×rows 的网格图（2×2 = 每张含4帧）。
    16帧 → 4张网格图。用 Pillow 拼接。
    """
    from PIL import Image

    group_size = cols * rows
    grids = []

    for g_idx in range(0, len(frames), group_size):
        group = frames[g_idx: g_idx + group_size]
        if not group:
            break

        imgs = [Image.open(p) for p in group]
        w, h = imgs[0].size

        # 不足 4 帧时用黑色填充
        while len(imgs) < group_size:
            imgs.append(Image.new("RGB", (w, h), (0, 0, 0)))

        grid = Image.new("RGB", (w * cols, h * rows))
        for i, img in enumerate(imgs):
            # 统一尺寸（少数帧可能因视频分辨率变化而不同）
            if img.size != (w, h):
                img = img.resize((w, h))
            col = i % cols
            row = i // cols
            grid.paste(img, (col * w, row * h))

        grid_path = os.path.join(out_dir, f"grid_{g_idx // group_size:02d}.jpg")
        grid.save(grid_path, "JPEG", quality=80)
        grids.append(grid_path)

        for img in imgs:
            img.close()

    print(f"[vision] 拼成 {len(grids)} 张 {cols}×{rows} 网格图")
    return grids


def _encode_image_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _call_vlm(grid_images: list[str]) -> str:
    """
    单次请求发送所有网格图给 VLM，保持完整视频上下文。
    网格图数量通常只有 4 张（16帧÷4），远少于原来的 16 张，
    VLM 图像编码器开销减少约 4 倍。
    """
    if not SILICONFLOW_API_KEY:
        raise RuntimeError("未配置 SILICONFLOW_API_KEY 环境变量")
    if not grid_images:
        return ""

    contents = []
    for path in grid_images:
        b64 = _encode_image_base64(path)
        contents.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{b64}",
            },
        })
    contents.append({
        "type": "text",
        "text": _RECIPE_VISION_PROMPT,
    })

    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": contents}],
        "max_tokens": 1500,
        "temperature": 0.1,
    }

    t0 = time.time()
    print(f"[vision] 发送 {len(grid_images)} 张网格图给 {VLM_MODEL}...")
    resp = requests.post(
        VLM_API_URL,
        headers={
            "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"].strip()
    print(f"[vision] VLM 返回 {len(text)} 字，用时 {time.time() - t0:.1f}s")
    return text


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
    主入口：抽帧 → 拼网格 → 单次 VLM → 菜谱文本
    失败返回空字符串，不抛异常
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

            grids = _create_grids(frames, tmpdir)
            if not grids:
                print("[vision] 网格拼接失败，跳过 VLM 分析")
                return ""

            result = _call_vlm(grids)

            if not _is_useful_result(result):
                print(f"[vision] VLM 返回内容不含菜谱信息，忽略: {result[:60]}...")
                return ""

            print(f"[vision] 分析完成，总用时 {time.time() - t_total:.1f}s")
            return result

    except Exception as e:
        print(f"[vision] VLM 分析失败: {e}")
        return ""
