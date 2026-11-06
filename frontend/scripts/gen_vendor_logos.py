"""
生成模型广场的厂商图标（Gemini / Grok）。

为什么现画而不是找官方素材
    官方 Logo 有各自的商标与使用规范，随包分发需要确认授权。
    这里画的是**几何化近似标识**：只用于帮助用户辨认厂商，不做商用标识使用。

产出（640×640，与其它厂商图标同规格）
    frontend/src/assets/vendors/gemini.png      浅色主题
    frontend/src/assets/vendors/gemini-dark.png  深色主题
    frontend/src/assets/vendors/grok.png
    frontend/src/assets/vendors/grok-dark.png

用法：python frontend/scripts/gen_vendor_logos.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "frontend" / "src" / "assets" / "vendors"
SIZE = 640


def _sparkle(cx: float, cy: float, r: float, waist: float = 0.72, sharp: float = 0.62) -> list[tuple[float, float]]:
    """四芒星（Gemini 那种）：四个尖角 + 内凹的腰。

    极坐标构造：ρ(θ) = R·(1 − k·|sin 2θ|^p)
      · θ = 0/90/180/270 时 |sin2θ| = 0 → 尖角
      · θ = 45/135/… 时 = 1 → 收到腰（k 越大腰越细）
      · p < 1 时尖角是「尖」的而不是圆的（p=1 会退化成椭圆瓣）
    """
    pts: list[tuple[float, float]] = []
    steps = 720
    for i in range(steps):
        theta = 2 * math.pi * i / steps
        rho = r * (1 - waist * abs(math.sin(2 * theta)) ** sharp)
        pts.append((cx + rho * math.cos(theta), cy + rho * math.sin(theta)))
    return pts


def _gradient(size: int, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    """左上 → 右下的线性渐变。"""
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = (
                int(c1[0] + (c2[0] - c1[0]) * t),
                int(c1[1] + (c2[1] - c1[1]) * t),
                int(c1[2] + (c2[2] - c1[2]) * t),
                255,
            )
    return grad


def make_gemini(dark: bool) -> Image.Image:
    size = SIZE
    big = size * 2  # 2 倍画布再缩小，边缘更干净
    shape = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(shape)
    # 圆心在画布中心，半径按 big 算（不要用 size，再乘 2 就错位了）
    d.polygon(_sparkle(big / 2, big / 2, big * 0.46), fill=255)
    shape = shape.resize((size, size), Image.LANCZOS)

    base = _gradient(size, (66, 133, 244), (155, 114, 203))  # #4285F4 → #9B72CB
    if dark:
        base = _gradient(size, (138, 180, 248), (191, 166, 226))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(base, (0, 0), shape)
    return out


def make_grok(dark: bool) -> Image.Image:
    """Grok：两笔交叠的圆角粗杠（近似其「𝕏」标识）。"""
    size = SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (255, 255, 255, 255) if dark else (16, 20, 28, 255)

    w = int(size * 0.155)
    length = int(size * 0.62)
    margin = (size - length) // 2
    # 两条对角线
    for sign in (-1, 1):
        # 用「厚线段 + 两端圆帽」拼出圆角效果
        cx, cy = size / 2, size / 2
        half = length / 2
        dx, dy = math.hypot(1, sign) , sign  # 占位，下面直接算端点
        ex, ey = half / math.sqrt(2), sign * half / math.sqrt(2)
        p0 = (cx - ex, cy - ey)
        p1 = (cx + ex, cy + ey)
        d.line([p0, p1], fill=color, width=w)
        r = w / 2
        for px, py in (p0, p1):
            d.ellipse([px - r, py - r, px + r, py + r], fill=color)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, maker in (("gemini", make_gemini), ("grok", make_grok)):
        for suffix, dark in (("", False), ("-dark", True)):
            path = OUT / f"{name}{suffix}.png"
            maker(dark).save(path, optimize=True)
            print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
