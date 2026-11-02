"""把 index.html 里最新的 logo / favicon data URI 同步到 developers.html。

两个页面各自内联一份（都不依赖外部文件），所以换 logo 时必须两边一起更新，
否则会出现「首页图标对齐了、开发者页还是老图」。
"""

import pathlib
import re

LANDING = pathlib.Path(__file__).resolve().parents[1]
index_html = (LANDING / "index.html").read_text(encoding="utf-8")
dev_p = LANDING / "developers.html"
dev = dev_p.read_text(encoding="utf-8")

logo = re.search(r'--logo: url\("data:image/png;base64,[^"]+"\);', index_html)
assert logo, "index.html 里没有 --logo"
dev, n1 = re.subn(r'--logo: url\("data:image/png;base64,[^"]+"\);', logo.group(0), dev)

links = re.findall(r'^<link rel="(?:icon|apple-touch-icon)"[^>]*>$', index_html, re.M)
assert len(links) >= 2, "index.html 里缺 favicon"
dev, n2 = re.subn(
    r'^<link rel="(?:icon|apple-touch-icon)"[^>]*>$',
    lambda m, it=iter(links): next(it, m.group(0)),
    dev,
    flags=re.M,
)

dev_p.write_text(dev, encoding="utf-8")
print(f"developers.html 更新：--logo {n1} 处，favicon {n2} 处")
