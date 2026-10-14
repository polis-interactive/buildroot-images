#!/usr/bin/env python3
"""Keep iwd profiles and the system-bus identity on the persistent /var/lib mount."""
from pathlib import Path
import sys


def install(target):
    dbus = target / 'var/lib/dbus'
    # Buildroot's BusyBox integration otherwise redirects machine-id to /tmp.
    if dbus.is_symlink():
        dbus.unlink()
    dbus.mkdir(parents=True, exist_ok=True)
    iwd = target / 'var/lib/iwd'
    iwd.mkdir(parents=True, exist_ok=True)
    iwd.chmod(0o700)


if __name__ == '__main__':
    install(Path(sys.argv[1]))
