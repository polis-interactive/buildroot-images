#!/usr/bin/env python3
"""Remove retired USB graphics files from every profile's reused target tree."""
from pathlib import Path
import shutil
import sys

LEGACY_FILES = (
    'usr/bin/hdmi-graphics-hello',
    'usr/sbin/polis-graphics-usb',
    'etc/init.d/S09polis-graphics-usb',
)
LEGACY_RULE = 'ttyACM[0-9]+ root:root 660 */usr/sbin/polis-graphics-usb event'


def install(target):
    for name in LEGACY_FILES:
        (target / name).unlink(missing_ok=True)
    assets = target / 'usr/share/polis-graphics-usb'
    if assets.is_symlink():
        assets.unlink()
    elif assets.exists():
        shutil.rmtree(assets)
    config = target / 'etc/mdev.conf'
    if config.exists():
        lines = config.read_text().splitlines(keepends=True)
        config.write_text(''.join(line for line in lines if line.strip() != LEGACY_RULE))


if __name__ == '__main__':
    install(Path(sys.argv[1]))
