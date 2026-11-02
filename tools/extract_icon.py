#!/usr/bin/env python3
"""从 PE 里提取 GROUP_ICON 指向的图标，重组成 .ico 并转 PNG。

用来确认「图标到底有没有换成我们的」——只看 exe 体积变大不算证据。
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def sections_and_res(data: bytes):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    coff = pe + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    is_plus = struct.unpack_from("<H", data, opt)[0] == 0x20B
    dd = opt + (112 if is_plus else 96)
    res_rva = struct.unpack_from("<I", data, dd + 2 * 8)[0]

    sec = opt + opt_size
    secs = []
    for i in range(nsec):
        b = sec + i * 40
        va = struct.unpack_from("<I", data, b + 12)[0]
        raw_size, raw_ptr = struct.unpack_from("<II", data, b + 16)
        secs.append((va, raw_ptr, raw_size))

    def off_of(rva: int):
        for va, ptr, size in secs:
            if va <= rva < va + max(size, 1):
                return ptr + (rva - va)
        return None

    return off_of(res_rva)


def walk_res(data: bytes, base_off: int):
    """返回 {(type_id, name_id): 资源字节}，只收 ICON / GROUP_ICON。"""
    out: dict[tuple[int, int], bytes] = {}

    def walk(off: int, depth: int, cur_type: int | None, cur_name: int | None):
        named, ids = struct.unpack_from("<HH", data, off + 12)
        for i in range(named + ids):
            ent = off + 16 + i * 8
            name_field, data_field = struct.unpack_from("<II", data, ent)
            ident = name_field & 0xFFFF
            if depth == 0:
                walk(base_off + (data_field & 0x7FFFFFFF), 1, ident, None)
            elif depth == 1:
                walk(base_off + (data_field & 0x7FFFFFFF), 2, cur_type, ident)
            else:
                if data_field & 0x80000000:
                    continue
                d = base_off + data_field
                rva, size = struct.unpack_from("<II", data, d)
                if cur_type in (3, 14):  # ICON / GROUP_ICON
                    out.setdefault((cur_type, cur_name), data[d + 8:d + 8 + size] if False else None)
                    # 数据在文件里的偏移 = rva 转换后的位置
                    out[(cur_type, cur_name)] = (rva, size)

    walk(base_off, 0, None, None)
    return out


def main(exe: Path, out_ico: Path) -> None:
    data = exe.read_bytes()
    base_off = sections_and_res(data)
    if base_off is None:
        raise SystemExit("找不到资源目录")

    # 重新取一遍，带 rva→offset 换算
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    coff = pe + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    is_plus = struct.unpack_from("<H", data, opt)[0] == 0x20B
    dd = opt + (112 if is_plus else 96)
    res_rva = struct.unpack_from("<I", data, dd + 2 * 8)[0]

    sec = opt + opt_size
    secs = []
    for i in range(nsec):
        b = sec + i * 40
        va = struct.unpack_from("<I", data, b + 12)[0]
        raw_size, raw_ptr = struct.unpack_from("<II", data, b + 16)
        secs.append((va, raw_ptr, raw_size))

    def off_of(rva: int):
        for va, ptr, size in secs:
            if va <= rva < va + max(size, 1):
                return ptr + (rva - va)
        return None

    icons: dict[int, bytes] = {}
    groups: list[bytes] = []

    def walk(off: int, depth: int, cur_type: int | None, cur_name: int | None):
        named, ids = struct.unpack_from("<HH", data, off + 12)
        for i in range(named + ids):
            ent = off + 16 + i * 8
            name_field, data_field = struct.unpack_from("<II", data, ent)
            ident = name_field & 0xFFFF
            if depth == 0:
                walk(base_off + (data_field & 0x7FFFFFFF), 1, ident, None)
            elif depth == 1:
                walk(base_off + (data_field & 0x7FFFFFFF), 2, cur_type, ident)
            else:
                if data_field & 0x80000000:
                    continue
                d = base_off + data_field
                rva, size = struct.unpack_from("<II", data, d)
                foff = off_of(rva)
                if foff is None:
                    continue
                blob = data[foff:foff + size]
                if cur_type == 3:
                    icons[ident] = blob
                elif cur_type == 14:
                    groups.append(blob)

    walk(base_off, 0, None, None)
    if not groups:
        raise SystemExit("没有 GROUP_ICON")
    grp = groups[0]
    count = struct.unpack_from("<H", grp, 4)[0]
    print(f"GROUP_ICON 含 {count} 个尺寸：")

    entries = []
    for i in range(count):
        w, h, cc, rsv, planes, bits, nbytes, nid = struct.unpack_from("<BBBBHHIH", grp, 6 + i * 14)
        w = w or 256
        h = h or 256
        blob = icons.get(nid)
        print(f"  {w}x{h}  {bits}bpp  id={nid}  {nbytes} bytes  {'OK' if blob else '缺失'}")
        if blob:
            entries.append((w, h, bits, blob))

    # 组装成标准 .ico：头 + 目录项 + 各图像数据
    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    dirs = b""
    blobs = b""
    for w, h, bits, blob in entries:
        dirs += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, bits, len(blob), offset)
        offset += len(blob)
        blobs += blob
    out_ico.write_bytes(header + dirs + blobs)
    print(f"\n已重组 {len(entries)} 个图像 -> {out_ico}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
