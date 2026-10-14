import json

from app.document import load_any
from app.document.ocr import (
    available_languages,
    normalize_cjk_spacing,
    probe,
    resolve_tesseract_cmd,
    resolve_tessdata_prefix,
)

print("=== probe() ===")
print(json.dumps(probe(refresh=True), ensure_ascii=False, indent=2))

print("=== resolve ===")
cmd = resolve_tesseract_cmd()
prefix = resolve_tessdata_prefix(cmd)
print("cmd   :", cmd)
print("prefix:", prefix)
print("langs :", available_languages(prefix))

print("=== normalize_cjk_spacing 演示 ===")
raw = "本 地 知 识 库 问 答 系 统\nTesseract OCR 中 文 识 别 测 试 2026\nHello World 12345"
print("before:", repr(raw))
print("after :", repr(normalize_cjk_spacing(raw)))

print("=== load_any() 真实图片 ===")
path = r"C:\Users\a1731\AppData\Local\Temp\tess\sample.png"
docs = load_any(path)
print("docs:", len(docs))
if docs:
    print(repr(docs[0].page_content))
    print("meta:", json.dumps(docs[0].metadata, ensure_ascii=False))
