#!/usr/bin/env python3
"""Buildroot host-side check; does not require Python in the target image."""
import subprocess
import os
import sys
from pathlib import Path

PACKAGE_PROGRAMS = {
    'BR2_PACKAGE_IWD': ('usr/libexec/iwd',),
    'BR2_PACKAGE_DBUS': ('usr/bin/dbus-daemon', 'usr/bin/dbus-uuidgen'),
    'BR2_PACKAGE_KMOD_TOOLS': ('usr/bin/kmod',),
    'BR2_PACKAGE_XZ': ('usr/bin/xz', 'usr/bin/xzdec', 'usr/bin/lzmadec', 'usr/bin/lzmainfo'),
    'BR2_PACKAGE_POLIS_BOOT_DIAGNOSTICS': ('usr/bin/polis-boot-diagnostics', 'usr/bin/vclog', 'usr/bin/vcgencmd'),
}


def selected_programs(config):
    programs = ['bin/busybox']
    selected = set(config.splitlines())
    for symbol, paths in PACKAGE_PROGRAMS.items():
        if symbol + '=y' in selected:
            programs.extend(paths)
    return programs


def main():
    target = Path(sys.argv[1])
    config = os.environ.get('BR2_CONFIG')
    if not config:
        raise RuntimeError('BR2_CONFIG must identify the selected Buildroot configuration')
    programs = selected_programs(Path(config).read_text())
    for program in programs:
        path = target / program
        if not path.is_file():
            raise RuntimeError(f"missing {program}")
        headers = subprocess.run(["readelf", "-lW", str(path)], check=True,
                                 capture_output=True, text=True).stdout
        dynamic = subprocess.run(["readelf", "-dW", str(path)], check=True,
                                 capture_output=True, text=True).stdout
        if "INTERP" in headers or "(NEEDED)" in dynamic:
            raise RuntimeError(f"{program} still needs dynamic linking")
    print("Polis: core boot executables passed the static-link check.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Polis static-link check: {error}", file=sys.stderr)
        sys.exit(1)
