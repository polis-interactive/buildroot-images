#!/usr/bin/env python3
"""Prepend the graphics rule without replacing Buildroot's standard mdev rules."""
from pathlib import Path
import sys

RULE = 'ttyACM[0-9]+ root:root 660 */usr/sbin/polis-graphics-usb event'


def install(target):
    path = target / 'etc/mdev.conf'
    lines = [line for line in path.read_text().splitlines() if line != RULE]
    path.write_text(RULE + '\n' + '\n'.join(lines) + '\n')


if __name__ == '__main__':
    install(Path(sys.argv[1]))
