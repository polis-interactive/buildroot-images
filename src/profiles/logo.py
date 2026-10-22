"""Prepare the kernel logo on the host; unchanged artwork needs no converter."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from ..common import UserError

SOURCE = Path('profiles/groups/rpi4-video/splash/polis-splash.png')
OPTIONS = ['-dither', 'None', '-colors', '224', '-depth', '8', '-compress', 'none']


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_ppm(path):
    tokens = ' '.join(line.split('#', 1)[0] for line in path.read_text().splitlines()).split()
    if tokens[:4] != ['P3', '512', '512', '255']:
        raise UserError(f'{path}: kernel logo must be a 512x512 ASCII RGB PPM (maxval 255)')
    channels = list(map(int, tokens[4:]))
    if (len(channels) != 512 * 512 * 3
            or any(value < 0 or value > 255 for value in channels)
            or len(set(zip(channels[::3], channels[1::3], channels[2::3]))) > 224):
        raise UserError(f'{path}: kernel logo needs exactly 512x512 pixels and at most 224 colors')


def prepare(source, output=None, *, check=False):
    """Return whether conversion was needed; preserve valid output on failures."""
    source = Path(source)
    output = Path(output) if output else source.with_suffix('.ppm')
    metadata = output.with_suffix('.json')
    png = source.read_bytes()
    if (png[:8] != b'\x89PNG\r\n\x1a\n' or len(png) < 24
            or struct.unpack('>II', png[16:24]) != (512, 512)):
        raise UserError(f'{source}: expected a 512x512 PNG')
    expected = dict(source_sha256=hashlib.sha256(png).hexdigest(), options=OPTIONS)
    try:
        recorded = json.loads(metadata.read_text())
        current = recorded == dict(expected, output_sha256=sha256(output))
    except (OSError, ValueError):
        current = False
    if current:
        return False
    if check:
        raise UserError(f'{output}: missing or stale; run python3 -m src.profiles.logo')
    converter = shutil.which('magick') or shutil.which('convert')
    if not converter:
        raise UserError('Kernel logo changed: install ImageMagick on the build host, '
                        'then run python3 -m src.profiles.logo. No target package is needed.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.polis-logo-', dir=output.parent) as temp:
        converted = Path(temp) / 'logo.ppm'
        record = Path(temp) / 'logo.json'
        subprocess.run([converter, str(source.resolve()), *OPTIONS, str(converted)], check=True)
        validate_ppm(converted)
        if sha256(source) != expected['source_sha256']:
            raise UserError(f'{source}: changed during conversion; retry')
        record.write_text(json.dumps(dict(expected, output_sha256=sha256(converted)), indent=2) + '\n')
        converted.replace(output)
        record.replace(metadata)
    print(f'Kernel logo converted: {output}')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[2] / SOURCE)
    parser.add_argument('--output', type=Path, help='PPM destination (default: beside source)')
    parser.add_argument('--check', action='store_true', help='check freshness without converting')
    args = parser.parse_args()
    try:
        if not prepare(args.source, args.output, check=args.check):
            print('Kernel logo is current; no conversion needed.')
    except (UserError, OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
