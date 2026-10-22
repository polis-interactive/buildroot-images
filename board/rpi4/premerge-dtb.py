#!/usr/bin/env python3
"""Bake the profile's ordered overlays into its DTB; retain firmware fixups."""

from pathlib import Path
import subprocess
import sys
import shutil


def merge(images, output, build, host, selections):
    work = build / "polis-premerge-dtb"
    work.mkdir(exist_ok=True)
    tool = host / "bin/dtmerge"
    if not tool.is_file():
        raise SystemExit("Missing host dtmerge; select BR2_PACKAGE_HOST_POLIS_DTMERGE")
    overlays = images / "rpi-firmware/overlays"
    source = images / "bcm2711-rpi-4-b.dtb"
    for index, selection in enumerate(selections):
        name, *parameters = selection.split(',')
        overlay = overlays / (name + '.dtbo')
        if not overlay.is_file():
            raise SystemExit(f"Missing selected firmware overlay: {overlay}")
        merged = work / f"overlay-{index}.dtb"
        subprocess.run([str(tool), str(source), str(merged), str(overlay),
                        *parameters], check=True)
        source = merged
    shutil.copyfile(source, output)


def main():
    images, output, build, host = map(Path, sys.argv[1:5])
    merge(images, output, build, host, sys.argv[5:])


if __name__ == "__main__":
    main()
