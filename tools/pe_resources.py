#!/usr/bin/env python3
"""列出 PE 文件里的资源目录（类型 / 名称 / 语言 / 大小）。

用途：确认 rcedit 到底把图标和版本信息写到哪了。
IExpress 生成的 exe 自带一份 VS_VERSIONINFO，rcedit 若另加一份，
Windows 资源管理器读的仍是第一份 —— 只看「rcedit 能回读」会得出错误结论。

无第三方依赖，直接按 PE 规范解析。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

RT = {
    1: "CURSOR", 2: "BITMAP", 3: "ICON", 4: "MENU", 5: "DIALOG",
    6: "STRING", 9: "ACCELERATOR", 10: "RCDATA", 11: "MESSAGETABLE",
    12: "GROUP_CURSOR", 14: "GROUP_ICON", 16: "VERSION", 17: "DLGINCLUDE",
    19: "PLUGPLAY", 20: "VXD", 21: "ANICURSOR", 22: "ANIICON", 23: "HTML",
    24: "MANIFEST",
}


def parse(path: Path) -> None:
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise SystemExit("不是 PE 文件")

    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_off:pe_off + 4] != b"PE\0\0":
        raise SystemExit("PE 签名不正确")

    coff = pe_off + 4
    machine, nsec = struct.unpack_from("<HH", data, coff)
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    is_plus = magic == 0x20B

    # 数据目录起始：PE32 在可选头 +96，PE32+ 在 +112
    dd = opt + (112 if is_plus else 96)
    res_rva, res_size = struct.unpack_from("<II", data, dd + 2 * 8)
    if res_rva == 0:
        raise SystemExit("没有资源目录")

    sec = opt + opt_size
    sections = []
    for i in range(nsec):
        base = sec + i * 40
        name = data[base:base + 8].rstrip(b"\0").decode("ascii", "replace")
        vaddr, vsize = struct.unpack_from("<II", data, base + 8)[0], struct.unpack_from("<I", data, base + 8)[0]
        raw_size, raw_ptr = struct.unpack_from("<II", data, base + 16)
        va = struct.unpack_from("<I", data, base + 12)[0]
        sections.append((name, va, raw_ptr, raw_size, struct.unpack_from("<I", data, base + 8)[0]))

    def rva_to_off(rva: int) -> int | None:
        for _name, va, raw_ptr, raw_size, _vsize in sections:
            if va <= rva < va + max(raw_size, 1):
                return raw_ptr + (rva - va)
        return None

    base_off = rva_to_off(res_rva)
    if base_off is None:
        raise SystemExit("资源目录不在任何节内")

    print(f"文件     {path.name}")
    print(f"架构     {'PE32+' if is_plus else 'PE32'}  (machine=0x{machine:04X})")
    print(f"资源目录 RVA=0x{res_rva:X}  size={res_size}")
    print()

    found: list[tuple[str, str, str, int]] = []

    def walk(off: int, depth: int, type_name: str, name: str) -> None:
        named, ids = struct.unpack_from("<HH", data, off + 12)
        for i in range(named + ids):
            ent = off + 16 + i * 8
            name_field, data_field = struct.unpack_from("<II", data, ent)
            if name_field & 0x80000000:
                # 具名条目：指向 UTF-16 字符串
                s_off = base_off + (name_field & 0x7FFFFFFF)
                ln = struct.unpack_from("<H", data, s_off)[0]
                cur = data[s_off + 2:s_off + 2 + ln * 2].decode("utf-16-le", "replace")
            else:
                cur = f"#{name_field & 0xFFFF}"

            if depth == 0:
                walk(base_off + (data_field & 0x7FFFFFFF), 1, cur, "")
            elif depth == 1:
                walk(base_off + (data_field & 0x7FFFFFFF), 2, type_name, cur)
            else:
                if data_field & 0x80000000:
                    continue
                d_off = base_off + data_field
                d_rva, d_size = struct.unpack_from("<II", data, d_off)
                found.append((type_name, name, cur, d_size))

    walk(base_off, 0, "", "")

    from collections import Counter
    counts = Counter(t for t, _, _, _ in found)
    for t, n, lang, size in found:
        label = RT.get(int(t[1:]), t) if t.startswith("#") else t
        print(f"  {label:<14} name={n:<8} lang={lang:<8} size={size}")

    print()
    print("汇总：")
    for t, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        label = RT.get(int(t[1:]), t) if t.startswith("#") else t
        print(f"  {label:<14} x {c}")
    if counts.get("#16", 0) > 1:
        print("\n注意：存在多个 RT_VERSION 资源块 —— Windows 只读第一块。")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if target is None:
        raise SystemExit("用法：python pe_resources.py <exe>")
    parse(target)
