#!/usr/bin/env python3
"""Wrap a raw, instrumented Pi4 ARM64 Image without modifying its bytes."""
import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def build(image, output, host, work, emulator=False):
    source = Path(__file__).resolve().parent
    image, output, host, work = [Path(p).resolve() for p in (image, output, host, work)]
    raw = image.read_bytes()
    if len(raw) < 64 or raw[56:60] != b'ARM\x64':
        raise ValueError('expected a raw ARM64 Image')
    offset, memory_size, flags = struct.unpack_from('<QQQ', raw, 8)
    if offset != 0 or flags != 10 or not len(raw) <= memory_size <= 64 * 1024**2:
        raise ValueError('only little-endian 4K Pi4 Images with zero text_offset are supported')
    library = ctypes.CDLL(str(host / 'lib/liblz4.so'))
    library.LZ4_compressBound.argtypes = [ctypes.c_int]
    library.LZ4_compress_HC.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    library.LZ4_decompress_safe.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    buffer = ctypes.create_string_buffer(library.LZ4_compressBound(len(raw)))
    count = library.LZ4_compress_HC(raw, buffer, len(raw), len(buffer), 9)
    if count <= 0:
        raise ValueError('LZ4 compression failed')
    compressed = buffer.raw[:count]
    decoded = ctypes.create_string_buffer(len(raw))
    if library.LZ4_decompress_safe(compressed, decoded, count, len(raw)) != len(raw) or decoded.raw != raw:
        raise ValueError('LZ4 round-trip did not match kernel')
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    relocation = (memory_size + 0x1fffff) & ~0x1fffff
    (work / 'payload.lz4').write_bytes(compressed)
    (work / 'payload.h').write_text(f'#define KERNEL_BYTES {len(raw)}\n#define COMPRESSED_BYTES {count}\n#define KERNEL_FLAGS {flags}\n#define RELOC_OFFSET {relocation}\n')
    compiler = str(host / 'bin/aarch64-linux-gcc')
    options = ['-O3', '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
               '-fno-pic', '-fno-pie', '-fno-asynchronous-unwind-tables',
               '-fno-unwind-tables', '-fno-tree-loop-distribute-patterns',
               '-mgeneral-regs-only', '-mcpu=cortex-a72', '-Wall', '-Wextra', '-Werror']
    for name in ('entry.S', 'decode.c'):
        subprocess.run([compiler, *options, '-I', str(work), *(['-DEMULATOR_TEST'] if emulator else []),
                        '-c', str(source / name), '-o', name + '.o'], cwd=work, check=True)
    subprocess.run([str(host / 'bin/aarch64-linux-ld'), '-T', str(source / 'loader.ld'),
                    '--no-undefined', '--orphan-handling=error', '-o', 'loader.elf',
                    'entry.S.o', 'decode.c.o'], cwd=work, check=True)
    subprocess.run([str(host / 'bin/aarch64-linux-objcopy'), '-O', 'binary',
                    str(work / 'loader.elf'), str(output)], check=True)
    wrapped = output.read_bytes()
    reservation = struct.unpack_from('<Q', wrapped, 16)[0]
    if len(wrapped) >= relocation or reservation != relocation + len(wrapped) + 8192:
        raise ValueError('invalid loader relocation/reservation layout')
    if not emulator and b'PLIS_ENTRY_V1' in wrapped[:8192]:
        raise ValueError('loader must not emit the Linux benchmark marker')
    metadata = dict(kernel_bytes=len(raw), kernel_memory_bytes=memory_size,
                    compressed_bytes=count, payload_bytes=len(wrapped),
                    relocation_offset=relocation, reserved_bytes=reservation,
                    kernel_sha256=hashlib.sha256(raw).hexdigest(),
                    payload_sha256=hashlib.sha256(wrapped).hexdigest(), emulator=emulator)
    output.with_suffix(output.suffix + '.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(metadata, indent=2))
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'output', 'host', 'work'):
        parser.add_argument(name)
    parser.add_argument('--emulator', action='store_true')
    args = parser.parse_args()
    build(args.image, args.output, args.host, args.work, args.emulator)
