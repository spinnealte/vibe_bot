"""Генерирует assets/default_cover.png — заглушку для курсов без фото (градиент 1280×720, без внешних библиотек).

Запуск из корня stubBot: python -m scripts.make_default_cover
Заглушку можно просто заменить своим баннером с тем же именем файла.
"""

import struct
import zlib
from pathlib import Path

WIDTH, HEIGHT = 1280, 720
TOP_LEFT = (14, 116, 144)  # бирюзовый
BOTTOM_RIGHT = (8, 47, 73)  # тёмно-синий
TARGET = Path("assets/default_cover.png")


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def build_png() -> bytes:
    rows = bytearray()
    for y in range(HEIGHT):
        rows.append(0)  # фильтр строки: None
        for x in range(WIDTH):
            t = (x / WIDTH + y / HEIGHT) / 2
            rows.extend(round(a + (b - a) * t) for a, b in zip(TOP_LEFT, BOTTOM_RIGHT))
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)  # 8 бит, RGB
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + _chunk(b"IEND", b""))


if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(build_png())
    print(f"Готово: {TARGET} ({TARGET.stat().st_size // 1024} КБ)")
