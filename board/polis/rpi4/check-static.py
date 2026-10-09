#!/usr/bin/env python3
"""Buildroot host-side check; does not require Python in the target image."""
import subprocess
import sys
from pathlib import Path

PROGRAMS = (
    "bin/busybox", "usr/libexec/iwd", "usr/bin/dbus-daemon",
    "usr/bin/dbus-uuidgen", "usr/bin/kmod", "usr/bin/xz", "usr/bin/xzdec",
    "usr/bin/lzmadec", "usr/bin/lzmainfo",
)


def main():
    target = Path(sys.argv[1])
    programs = list(PROGRAMS)
    for optional in ("usr/bin/polis-gpio-test", "usr/bin/vclog", "usr/bin/vcgencmd"):
        if (target / optional).is_file():
            programs.append(optional)
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
