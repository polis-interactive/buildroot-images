#!/usr/bin/env python3
"""Stage an already cross-built graphics app; never build or deploy it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tomllib

REPO = Path(__file__).resolve().parents[2]
STAGE = REPO / '.local/graphics-usb'
IDENTITY = dict(vid=0x2e8a, pid=0x0009, manufacturer='Polis',
                product='Polis Screen Graphics', interface=0)


def validate(binary, profile):
    data = binary.read_bytes()
    if (len(data) < 64 or data[:6] != b'\x7fELF\x02\x01'
            or struct.unpack_from('<H', data, 18)[0] != 183):
        raise ValueError(f'{binary}: expected little-endian AArch64 ELF')
    dynamic = subprocess.check_output(['readelf', '-d', str(binary)], text=True)
    needed = re.findall(r'\(NEEDED\).*\[(.*?)\]', dynamic)
    unexpected = set(needed) - {'libc.so', 'libgcc_s.so.1'}
    if unexpected:
        raise ValueError(f'Unapproved target dependencies: {sorted(unexpected)}')
    headers = subprocess.check_output(['readelf', '-l', str(binary)], text=True)
    interpreter = re.findall(r'Requesting program interpreter: (.*?)\]', headers)
    if interpreter and interpreter != ['/lib/ld-musl-aarch64.so.1']:
        raise ValueError(f'Unexpected interpreter: {interpreter}')
    if tomllib.loads(profile.read_text()) != IDENTITY:
        raise ValueError(f'{profile}: graphics descriptor contract changed; review integration')
    return dict(binary_sha256=hashlib.sha256(data).hexdigest(), needed=needed,
                profile_sha256=hashlib.sha256(profile.read_bytes()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=REPO.parent / '2050-hdmi-passthru')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        metadata = validate(STAGE / 'hdmi-graphics-hello', STAGE / 'screen-controller-graphics.toml')
        if metadata != json.loads((STAGE / 'manifest.json').read_text()):
            raise ValueError('Staged graphics payload changed; prepare it again')
    else:
        binary = args.app / '.target/aarch64-unknown-linux-musl/release/hdmi-graphics-hello'
        profile = args.app / 'assets/devices/screen-controller-graphics.toml'
        metadata = validate(binary, profile)
        STAGE.mkdir(parents=True, exist_ok=True)
        for source in (binary, profile):
            temp = STAGE / (source.name + '.tmp')
            shutil.copyfile(source, temp)
            temp.chmod(0o755 if source == binary else 0o644)
            temp.replace(STAGE / source.name)
        (STAGE / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Graphics payload verified: {STAGE}')


if __name__ == '__main__':
    main()
