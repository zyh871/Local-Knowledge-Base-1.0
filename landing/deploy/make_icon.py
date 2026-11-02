#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 logo 合成到纯白圆角底板上，产出应用图标与前端用的白底 logo。

为什么要加白底
    logo 本体是蓝色鲸鱼剪影，中间有白色镂空（腹部、浪花）。放在深色或
    彩色背景上时，镂空处会透出底色，整张图看起来「漏气」；做成白底圆角
    方块后，无论在浅色侧栏、深色侧栏还是 Windows 任务栏里，眼睛看到的
    都是设计稿那一版。

产出
    landing/deploy/app.ico                  Windows 图标（16–256，多尺寸）
    frontend/src/assets/zhijing-logo-white.png   应用内品牌标（512，白底圆角）

用法：python landing/deploy/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "landing" / "deploy" / "logo-source.png"
ICO = ROOT / "landing" / "deploy" / "app.ico"
PNG_OUT = ROOT / "frontend" / "src" / "assets" / "zhijing-logo-white.png"

ICO_SIZES = [256, 128, 64, 48, 32, 16]
#: 底板圆角半径 / 边长。0.22 接近 Windows 11 应用图标的观感
RADIUS_RATIO = 0.22
#: logo 占底板的比例（留白）
PADDING_RATIO = 0.10


def load_mark() -> Image.Image:
    """取出图形本体：按「墨迹」而不是「非全透明」裁边，再等比缩进正方形。"""
    img = Image.open(SRC).convert("RGBA")
    alpha = img.split()[-1]

    # ★ 用 alpha >= 40 判定墨迹，而不是 getbbox() 默认的 alpha > 0。
    #   这张图在右下角有一大片 alpha 只有 24–39 的残留（JPEG 压缩鬼影），
    #   肉眼看不见，却会被算进边界 —— 结果就是 logo 缩得又小、还整体偏上。
    ink = alpha.point(lambda v: 255 if v >= 40 else 0)
    bbox = ink.getbbox()
    if bbox:
        img = img.crop(bbox)
    # 注意：这里**不**把图形补成正方形。鲸鱼是横向的（约 1.7:1），
    # 硬补成正方形再缩放，图形只占中间一条，30px 的侧栏 logo 会小到看不清。
    return img


def compose(size: int) -> Image.Image:
    """白底圆角方块 + 居中的 logo。"""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 圆角底板：先在 4 倍尺寸上画圆角，再缩下来（直接画会有锯齿）
    scale = 4
    big = size * scale
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=int(big * RADIUS_RATIO), fill=255
    )
    plate = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    plate.putalpha(mask.resize((size, size), Image.LANCZOS))
    canvas.alpha_composite(plate)

    mark = load_mark()
    inner = max(1, int(size * (1 - PADDING_RATIO * 2)))
    # 等比缩放到内框里（长边贴边），再居中 —— 横向 logo 会占满宽度，
    # 上下自然留白，比强行拉成正方形清楚得多
    w0, h0 = mark.size
    scale = min(inner / w0, inner / h0)
    mark = mark.resize((max(1, round(w0 * scale)), max(1, round(h0 * scale))), Image.LANCZOS)
    canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    return canvas


def main() -> None:
    base = compose(512)
    PNG_OUT.parent.mkdir(parents=True, exist_ok=True)
    base.save(PNG_OUT, optimize=True)

    frames = [compose(s) for s in ICO_SIZES]
    ICO.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        ICO,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=frames[1:],
    )

    print(f"图标   {ICO.relative_to(ROOT)}   {ICO.stat().st_size / 1024:.1f} KB   {ICO_SIZES}")
    print(f"白底图 {PNG_OUT.relative_to(ROOT)}   {PNG_OUT.stat().st_size / 1024:.1f} KB   512×512")


if __name__ == "__main__":
    main()
