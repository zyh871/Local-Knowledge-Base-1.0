#!/usr/bin/env python
"""智谱 CogVideoX-3 视频生成 —— 可直接运行的独立脚本。

用途：不启动整套服务，也能验证「文本 → 视频」这条链是否通。
服务端走的是同一套协议（`app/rag/zhipu_video.py`）。

用法：
    # 最简：只给提示词（默认 1920x1080、5 秒、带音效、画质优先）
    python scripts/generate_video_cogvideox3.py --prompt "一只白色小鲸鱼在星空中缓慢游动"

    # 竖屏 + 速度优先 + 无音效
    python scripts/generate_video_cogvideox3.py \
        --prompt "雨夜的赛博朋克街道" --size 720x1280 --quality speed --no-audio

    # 图生视频（首帧）
    python scripts/generate_video_cogvideox3.py --prompt "让画面动起来" --image ./cover.png

    # 10 秒 + 60fps
    python scripts/generate_video_cogvideox3.py --prompt "..." --duration 10 --fps 60

API Key 读取顺序：--api-key → 环境变量 ZHIPU_API_KEY → backend/.env 的 ZHIPU_API_KEY。
"""

from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import sys
import time
from pathlib import Path

import httpx

BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "cogvideox-3"

#: 300 秒不够：实测 1080p 排队 + 生成约 2 分钟，高峰会更久
DEFAULT_TIMEOUT = 600
POLL_INTERVAL = 5


def load_api_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key.strip()
    key = (os.environ.get("ZHIPU_API_KEY") or "").strip()
    if key:
        return key
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("ZHIPU_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("未找到 ZHIPU_API_KEY：请用 --api-key 传入，或写入环境变量 / backend/.env")


def to_data_url(path: str) -> str:
    """本地图片转 data URL（智谱的 image_url 接受 URL 或 Base64）。"""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"图片不存在：{path}")
    if p.stat().st_size > 5 * 1024 * 1024:
        raise SystemExit("图片超过 5MB，智谱会拒绝（请先压缩）")
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    if mime not in ("image/png", "image/jpeg"):
        raise SystemExit(f"仅支持 png / jpeg，当前是 {mime}")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")


def explain(code: str, message: str) -> str:
    hints = {
        "1001": "鉴权失败：API Key 无效或已过期",
        "1002": "鉴权失败：缺少 API Key",
        "1113": "余额不足（视频生成按次计费）",
        "1211": "模型不存在或未开通",
        "1301": "内容安全审核未通过",
        "1305": "触发并发/频率限制，请稍后重试",
    }
    return hints.get(str(code), message or "未知错误")


def submit(client: httpx.Client, payload: dict) -> str:
    resp = client.post(f"{BASE_URL}/videos/generations", json=payload)
    data = _json(resp)
    if resp.status_code != 200:
        err = data.get("error") or {}
        raise SystemExit(
            f"提交失败 HTTP {resp.status_code}："
            f"{explain(err.get('code'), err.get('message'))}"
        )
    task_id = data.get("id") or data.get("request_id")
    if not task_id:
        raise SystemExit(f"提交成功但没有返回任务 id：{data}")
    print(f"· 任务已提交：{task_id}（状态 {data.get('task_status')}）")
    return str(task_id)


def poll(client: httpx.Client, task_id: str, timeout: int) -> dict:
    """轮询直到 SUCCESS / FAIL。task_status 只有三种取值。"""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        time.sleep(POLL_INTERVAL)
        resp = client.get(f"{BASE_URL}/async-result/{task_id}")
        data = _json(resp)
        status = str(data.get("task_status") or "").upper()
        elapsed = int(time.monotonic() - started)
        print(f"  [{elapsed:>3}s] {status or 'UNKNOWN'}", flush=True)

        if status == "SUCCESS":
            return data
        if status in ("FAIL", "FAILED"):
            err = data.get("error") or {}
            raise SystemExit(f"生成失败：{explain(err.get('code'), err.get('message'))}")
        if resp.status_code != 200:
            raise SystemExit(f"查询失败 HTTP {resp.status_code}：{data}")
    raise SystemExit(f"等待超时（{timeout}s），任务 {task_id} 可能仍在跑，可稍后用 id 再查")


def _json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:300]}


def download(client: httpx.Client, url: str, out: Path) -> int:
    """产物是带签名的临时链接，必须立刻落盘。"""
    resp = client.get(url, follow_redirects=True, timeout=300)
    if resp.status_code != 200:
        raise SystemExit(f"下载失败 HTTP {resp.status_code}")
    if not resp.content:
        raise SystemExit("下载到的文件为空")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(resp.content)
    return len(resp.content)


def main() -> int:
    parser = argparse.ArgumentParser(description="智谱 CogVideoX-3 文生视频 / 图生视频")
    parser.add_argument("--prompt", required=True, help="提示词（≤512 字符）")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--image", help="图生视频的首帧图片路径（png/jpeg，≤5MB）")
    parser.add_argument(
        "--size",
        default="1920x1080",
        choices=["1280x720", "720x1280", "1024x1024", "1920x1080", "1080x1920",
                 "2048x1080", "3840x2160"],
    )
    parser.add_argument("--quality", default="quality", choices=["quality", "speed"])
    parser.add_argument("--duration", type=int, default=5, choices=[5, 10])
    parser.add_argument("--fps", type=int, default=30, choices=[30, 60])
    parser.add_argument("--no-audio", action="store_true", help="关闭 AI 音效")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--out", default="output/cogvideox3.mp4")
    parser.add_argument("--api-key", help="不传则读 ZHIPU_API_KEY")
    args = parser.parse_args()

    prompt = args.prompt.strip()
    if len(prompt) > 512:
        print(f"! 提示词 {len(prompt)} 字，已截断到 512（智谱上限）")
        prompt = prompt[:512]

    payload: dict = {
        "model": args.model,
        "prompt": prompt,
        "quality": args.quality,
        "size": args.size,
        "duration": args.duration,
        "fps": args.fps,
        "with_audio": not args.no_audio,
    }
    if args.image:
        payload["image_url"] = to_data_url(args.image)

    headers = {
        "Authorization": f"Bearer {load_api_key(args.api_key)}",
        "Content-Type": "application/json",
    }
    print(f"→ 提交：model={args.model} size={args.size} duration={args.duration}s "
          f"fps={args.fps} quality={args.quality} audio={not args.no_audio}")

    with httpx.Client(headers=headers, timeout=60) as client:
        task_id = submit(client, payload)
        result = poll(client, task_id, args.timeout)
        results = result.get("video_result") or []
        if not results:
            raise SystemExit(f"任务成功但没有视频结果：{result}")
        item = results[0]
        out = Path(args.out)
        size_kb = download(client, item["url"], out) // 1024

    print(f"✓ 完成：{out}（{size_kb} KB）")
    print(f"  封面：{item.get('cover_image_url', '')[:80]}...")
    print(f"  注意：上游链接是带签名的临时地址，过期后无法再下载，请保留本地文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
