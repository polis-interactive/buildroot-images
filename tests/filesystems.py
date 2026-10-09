#!/usr/bin/env python3
"""Resolve the musl configs and exercise genimage with non-bootable fixtures.

No downloads, full builds, mounts, or block-device writes are performed.
"""

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


REPO = Path(__file__).resolve().parents[1]
VARIANTS = ("ext4", "erofs", "squashfs", "initramfs")


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args}\n{result.stdout!r}\n{result.stderr!r}")
    return result.stdout


def read_config(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line.startswith("BR2_") and "=" in line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buildroot", type=Path, required=True)
    parser.add_argument("--host-dir", type=Path, required=True,
                        help="built Buildroot host directory with genimage, e2fsprogs and mtools")
    args = parser.parse_args()
    buildroot, host = args.buildroot.resolve(), args.host_dir.resolve()
    env = dict(os.environ, PATH=f"{host / 'bin'}:{host / 'sbin'}:{os.environ['PATH']}")
    allowed = {
        "BR2_DEFCONFIG", "BR2_ROOTFS_POST_SCRIPT_ARGS",
        "BR2_PACKAGE_RPI_FIRMWARE_CMDLINE_FILE", "BR2_PACKAGE_RPI_FIRMWARE_CONFIG_FILE",
        "BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES", "BR2_PACKAGE_HOST_EROFS_UTILS",
    }
    expected = {
        "ext4": ("BR2_TARGET_ROOTFS_EXT2_4", "y"),
        "erofs": ("BR2_TARGET_ROOTFS_EROFS_LZ4HC", "y"),
        "squashfs": ("BR2_TARGET_ROOTFS_SQUASHFS4_LZ4", "y"),
        "initramfs": ("BR2_TARGET_ROOTFS_CPIO_LZ4", "y"),
    }
    baseline, layout = None, None
    with tempfile.TemporaryDirectory(prefix="polis-filesystems-") as tmp:
        tmp = Path(tmp)
        for variant in VARIANTS:
            board = "polis_rpi4_musl" + ("" if variant == "ext4" else f"_{variant}")
            out = tmp / board
            run(["make", "-s", "-C", str(buildroot), f"O={out}",
                 f"BR2_EXTERNAL={REPO}", f"{board}_defconfig"])
            config = read_config(out / ".config")
            key, value = expected[variant]
            assert config.get(key) == value, (variant, key)
            formats = {"ext4": "EXT2", "erofs": "EROFS", "squashfs": "SQUASHFS", "initramfs": "CPIO"}
            assert {name for name in formats.values()
                    if config.get(f"BR2_TARGET_ROOTFS_{name}") == "y"} == {formats[variant]}
            assert "BR2_TARGET_GENERIC_REMOUNT_ROOTFS_RW" not in config
            assert config.get("BR2_PACKAGE_HOST_E2FSPROGS") == "y"
            assert "BR2_TARGET_ROOTFS_TAR" not in config
            if variant == "squashfs":
                assert "BR2_TARGET_ROOTFS_SQUASHFS_EXTREME_COMP" not in config
            if variant == "initramfs":
                assert "BR2_TARGET_ROOTFS_INITRAMFS" not in config
            if baseline is None:
                baseline = config
            else:
                differences = {k for k in baseline.keys() | config.keys()
                               if baseline.get(k) != config.get(k)}
                assert not {k for k in differences if k not in allowed
                            and not k.startswith("BR2_TARGET_ROOTFS_")}, differences

            images = out / "images"
            firmware = images / "rpi-firmware"
            (firmware / "overlays").mkdir(parents=True)
            for name in ("start4.elf", "fixup4.dat", "overlays/disable-bt.dtbo"):
                (firmware / name).write_bytes(b"fixture " + name.encode())
            for name, key in (("config.txt", "CONFIG"), ("cmdline.txt", "CMDLINE")):
                path = config[f"BR2_PACKAGE_RPI_FIRMWARE_{key}_FILE"].strip('"')
                path = path.replace("$(BR2_EXTERNAL_POLIS_PATH)", str(REPO))
                shutil.copyfile(path, firmware / name)
            (images / "Image").write_bytes(b"fixture kernel")
            (images / "bcm2711-rpi-4-b.dtb").write_bytes(b"fixture dtb")
            root_name = "rootfs.cpio.lz4" if variant == "initramfs" else f"rootfs.{variant}"
            payload = (variant.encode() + bytes(4096))[:4096]
            (images / root_name).write_bytes(payload)
            cfg = config["BR2_ROOTFS_POST_SCRIPT_ARGS"].strip('"').split("-c ", 1)[1]
            cfg = cfg.replace("$(BR2_EXTERNAL_POLIS_PATH)", str(REPO))
            image_env = dict(env, BINARIES_DIR=str(images), BUILD_DIR=str(out / "build"),
                             BR2_CONFIG=str(out / ".config"))
            run(["bash", str(buildroot / "support/scripts/genimage.sh"),
                 str(out / "target"), "-c", cfg], env=image_env)

            with (images / "sdcard.img").open("rb") as disk:
                mbr = disk.read(512)
                assert mbr[510:] == b"\x55\xaa"
                entries = [struct.unpack_from("<B3sB3sII", mbr, 446 + i * 16)
                           for i in range(4)]
                current = [(entry[0], entry[2], entry[4], entry[5]) for entry in entries]
                assert [entry[2] for entry in entries] == [0xC, 0x83, 0x83, 0]
                assert [entry[5] * 512 for entry in entries[:3]] == [128 << 20, 120 << 20, 64 << 20]
                if layout is None:
                    layout = current
                assert current == layout, (variant, current, layout)
                disk.seek(entries[2][4] * 512 + 1024 + 56)
                assert disk.read(2) == b"\x53\xef", "data partition is not ext4"
                disk.seek(entries[2][4] * 512 + 1024 + 120)
                assert disk.read(16).rstrip(b"\0") == b"polis-data"
                if variant != "initramfs":
                    disk.seek(entries[1][4] * 512)
                    assert disk.read(len(payload)) == payload
            for name in ("Image", "bcm2711-rpi-4-b.dtb", "start4.elf", "fixup4.dat",
                         "config.txt", "cmdline.txt", "overlays/disable-bt.dtbo"):
                assert run([str(host / "bin/mtype"), "-i", str(images / "boot.vfat"),
                            f"::{name}"], env=env)
            if variant == "initramfs":
                assert run([str(host / "bin/mtype"), "-i", str(images / "boot.vfat"),
                            "::rootfs.cpio.lz4"], env=env) == payload
                assert "initramfs rootfs.cpio.lz4 followkernel" in (firmware / "config.txt").read_text()
                def settings(path):
                    return [line for line in path.read_text().splitlines()
                            if line and not line.startswith("#")]
                assert settings(firmware / "config.txt") == settings(REPO / "board/polis/rpi4/config.txt") + [
                    "initramfs rootfs.cpio.lz4 followkernel"]
            print(f"PASS: {board}: resolved configuration, FAT contents, root payload, ext4 data, partition layout")


if __name__ == "__main__":
    main()
