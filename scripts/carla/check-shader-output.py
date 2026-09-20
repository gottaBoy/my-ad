#!/usr/bin/env python3
"""Check that shader smoke outputs contain the expected binary containers."""

from pathlib import Path
import struct
import sys


def check_spirv(data: bytes) -> None:
    if len(data) < 20 or len(data) % 4:
        raise ValueError("SPIR-V must contain a word-aligned header and instructions")
    magic, version, _, bound, schema = struct.unpack_from("<5I", data)
    if magic != 0x07230203 or not 0x00010000 <= version <= 0x00010600:
        raise ValueError("invalid SPIR-V magic or version")
    if not bound or schema:
        raise ValueError("invalid SPIR-V bound or schema")
    offset = 20
    entry_point = False
    while offset < len(data):
        word = struct.unpack_from("<I", data, offset)[0]
        word_count, opcode = word >> 16, word & 0xFFFF
        if not word_count or offset + word_count * 4 > len(data):
            raise ValueError("invalid SPIR-V instruction length")
        entry_point |= opcode == 15  # OpEntryPoint
        offset += word_count * 4
    if not entry_point:
        raise ValueError("SPIR-V has no entry point")


def check_dxil(data: bytes) -> None:
    if len(data) < 32 or data[:4] != b"DXBC":
        raise ValueError("missing DXIL container header")
    size, parts = struct.unpack_from("<2I", data, 24)
    if size != len(data) or not parts or 32 + parts * 4 > size:
        raise ValueError("invalid DXIL container size or part table")
    found = False
    for index in range(parts):
        offset = struct.unpack_from("<I", data, 32 + index * 4)[0]
        if offset < 32 + parts * 4 or offset + 8 > size:
            raise ValueError("invalid DXIL part offset")
        part_size = struct.unpack_from("<I", data, offset + 4)[0]
        if offset + 8 + part_size > size:
            raise ValueError("truncated DXIL part")
        if data[offset:offset + 4] == b"DXIL":
            if part_size < 24 or data[offset + 16:offset + 20] != b"DXIL":
                raise ValueError("invalid DXIL program header")
            found = True
    if not found:
        raise ValueError("container has no DXIL program")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: check-shader-output.py shader.spv shader.dxil")
    check_spirv(Path(sys.argv[1]).read_bytes())
    check_dxil(Path(sys.argv[2]).read_bytes())
    print("PASS shader containers: SPIR-V entry point and DXIL program")
