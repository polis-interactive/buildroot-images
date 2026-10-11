#!/usr/bin/env python3
"""Bake the appliance's two overlays into its DTB; retain firmware fixups."""

from pathlib import Path
import subprocess
import sys


def main():
    images, output, build, host = map(Path, sys.argv[1:])
    work = build / "polis-premerge-dtb"
    work.mkdir(exist_ok=True)
    tool = host / "bin/dtmerge"
    if not tool.is_file():
        raise SystemExit("Missing host dtmerge; select BR2_PACKAGE_HOST_POLIS_DTMERGE")
    overlays = images / "rpi-firmware/overlays"
    intermediate = work / "disable-bt.dtb"
    subprocess.run([str(tool), str(images / "bcm2711-rpi-4-b.dtb"),
                    str(intermediate), str(overlays / "disable-bt.dtbo")], check=True)
    subprocess.run([str(tool), str(intermediate), str(output),
                    str(overlays / "dwc2.dtbo"), "dr_mode=peripheral"], check=True)


if __name__ == "__main__":
    main()
