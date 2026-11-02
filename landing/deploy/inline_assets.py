"""把落地页从「HTML + 外部 assets」转成完全自包含的单文件。

做四件事：
  1. logo / favicon 内联为 data URI（只保留一份 base64，在 CSS 变量 --logo 里）
  2. 三个 <img src="assets/..."> 换成 CSS 背景的 <span class="logo">
  3. 去掉 og:image（单文件无法给出可抓取的绝对地址，留着只是死链）
  4. 删掉 landing/assets/ 与临时产物

换 logo 的流程：替换 deploy/logo-source.png → 重新跑本脚本。
脚本是幂等的：已经内联过的页面再跑一次结果不变。

执行后可验证：把 index.html 单独复制到任意空目录，断网打开仍然完整。
"""

from __future__ import annotations

import base64
import io
import re
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
LANDING = ROOT / "landing"
HTML = LANDING / "index.html"

#: logo 源图：优先用 deploy/logo-source.png（随仓库提供），
#: 兼容旧结构 landing/assets/logo-mark.png
CANDIDATES = [
    LANDING / "deploy" / "logo-source.png",
    LANDING / "assets" / "logo-mark.png",
]


def find_source() -> Path:
    for p in CANDIDATES:
        if p.exists():
            return p
    raise SystemExit("找不到 logo 源图，请放到 deploy/logo-source.png")


def data_uri(img: Image.Image, size: int) -> str:
    # 1) 裁掉透明留白。
    #
    # ★ 判据是「墨迹」而不是「非全透明」：源图是 JPEG 转的 PNG，右下角有一片
    #   alpha 只有 24–39 的残留，肉眼看不见，却会被 getbbox() 算进边界。
    #   结果补出来的正方形底部空一大截，CSS 居中之后图形整体偏高 ——
    #   表现就是「独角鲸图标和文字不在一条线上」。
    alpha = img.split()[-1]
    ink = alpha.point(lambda v: 255 if v >= 40 else 0)
    bbox = ink.getbbox()
    if bbox:
        img = img.crop(bbox)

    # 2) 补成正方形再缩放。logo 是横向的（宽 > 高），直接塞进正方形框会被
    #    contain 压得偏小、偏扁；补边后无论 <span> 是方是圆都能居中铺满，
    #    favicon 也不会变成扁的。
    w, h = img.size
    side = int(max(w, h) * 1.06)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)

    im = canvas.copy()
    im.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def main() -> None:
    src = Image.open(find_source()).convert("RGBA")
    logo = data_uri(src, 128)
    icon = data_uri(src, 48)

    html = HTML.read_text(encoding="utf-8")
    before = len(html)

    # 已经是内联形态（没有 assets 引用、也没有 --logo 占位）就只刷新 base64
    already_inline = "<span class=\"logo\"" in html

    # ── 1. head 里的图标 ──────────────────────────────────────────
    html = re.sub(
        r'<link rel="icon" href="(?:assets/favicon\.png|data:image/png;base64,[^"]*)" />',
        f'<link rel="icon" href="{icon}" />',
        html,
    )
    html = re.sub(
        r'<link rel="apple-touch-icon" href="(?:assets/logo-mark\.png|data:image/png;base64,[^"]*)" />',
        f'<link rel="apple-touch-icon" href="{icon}" />',
        html,
    )
    html = re.sub(
        r'\n<meta property="og:image" content="assets/logo-mark\.png" />', "", html
    )

    # ── 2. CSS 变量：整页唯一的 base64（重复执行时原地替换） ────────
    if "--logo: url(" in html:
        html = re.sub(
            r'--logo: url\("data:image/png;base64,[^"]*"\);',
            f'--logo: url("{logo}");',
            html,
        )
    else:
        anchor = '  --font-mono: "JetBrains Mono", "SFMono-Regular", Menlo, Consolas, monospace;\n'
        assert anchor in html, "找不到 --font-mono 锚点"
        html = html.replace(
            anchor,
            anchor
            + "\n  /* logo 内联为 data URI：整页因此不再依赖任何外部文件 */\n"
            + f'  --logo: url("{logo}");\n',
        )

    # ── 3. img → span.logo（首次执行才需要） ─────────────────────
    html, n_img = re.subn(
        r'<img src="assets/logo-mark\.png"[^>]*/>',
        '<span class="logo" aria-hidden="true"></span>',
        html,
    )
    if not already_inline:
        assert n_img == 3, f"预期替换 3 个 logo，实际 {n_img}"

    if ".brand img { width: 30px; height: 30px; }" in html:
        html = html.replace(
            ".brand img { width: 30px; height: 30px; }",
            """/* 品牌标志：用 CSS 背景承载内联的 logo，尺寸按场景覆盖 */
.logo {
  display: block;
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  background-image: var(--logo);
  background-repeat: no-repeat;
  background-position: center;
  background-size: contain;
}

.win-side-brand .logo { width: 18px; height: 18px; }
.footer-brand .logo { width: 28px; height: 28px; }""",
        )

    HTML.write_text(html, encoding="utf-8")

    # ── 4. 清理：单文件方案下 assets 与临时文件都不再需要 ──────────
    shutil.rmtree(LANDING / "assets", ignore_errors=True)
    for junk in ROOT.glob("_tmp_*.png"):
        junk.unlink()
    for junk in ROOT.glob("_tmp_*.b64"):
        junk.unlink()

    print(f"index.html: {before} -> {len(html)} 字符")
    print(f"logo base64 {len(logo)} 字符，icon base64 {len(icon)} 字符")
    print("页面已自包含，不再依赖 assets/")


if __name__ == "__main__":
    main()
