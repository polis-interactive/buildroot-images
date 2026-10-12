#!/usr/bin/env python3
"""Resolve both profiles and exercise genimage with nonbootable fixtures.

No downloads, compilation, mounts, or block-device writes are performed.
"""
import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.common import Context
from src.profiles import resolver, workflow
from src.profiles.image import recipe


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{args}\n{result.stdout!r}\n{result.stderr!r}')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--buildroot', type=Path, required=True)
    parser.add_argument('--host-dir', type=Path, required=True,
                        help='built host directory with genimage, e2fsprogs and mtools')
    args = parser.parse_args()
    buildroot, host = args.buildroot.resolve(), args.host_dir.resolve()
    env = dict(os.environ, PATH=f"{host / 'bin'}:{host / 'sbin'}:{os.environ['PATH']}")
    with tempfile.TemporaryDirectory(prefix='polis-profiles-') as directory:
        tmp = Path(directory)
        ctx = Context(REPO, buildroot, buildroot / 'dl', tmp / '.snapshots')
        for name in ctx.profiles:
            output = tmp / name
            plan = resolver.resolve(ctx, name)
            workflow.configure(ctx, plan, output, False)
            config = resolver.read_kconfig(output / '.config')
            assert config['BR2_TARGET_ROOTFS_EROFS_LZ4HC'] == 'y'
            assert config['BR2_PACKAGE_HOST_E2FSPROGS'] == 'y'
            assert config.get('BR2_TARGET_GENERIC_REMOUNT_ROOTFS_RW', 'n') == 'n'
            for other in ('EXT2', 'SQUASHFS', 'CPIO', 'TAR'):
                assert config.get('BR2_TARGET_ROOTFS_' + other, 'n') == 'n'
            images = output / 'images'
            firmware = images / 'rpi-firmware'
            (firmware / 'overlays').mkdir(parents=True)
            for filename in ('start4.elf', 'fixup4.dat', 'start4cd.elf', 'fixup4cd.dat', 'overlays/disable-bt.dtbo'):
                (firmware / filename).write_bytes(b'fixture ' + filename.encode())
            for filename in ('config.txt', 'cmdline.txt'):
                shutil.copy2(output / 'profile' / filename, firmware / filename)
            for filename in ('Image', 'Image.lz4boot', 'bcm2711-rpi-4-b.dtb', 'bcm2711-rpi-4-b.premerged.dtb'):
                (images / filename).write_bytes(b'fixture ' + filename.encode())
            payload = b'fixture erofs' + bytes(4096)
            (images / 'rootfs.erofs').write_bytes(payload)
            (images / 'boot-ramdisk-config.txt').write_text((firmware / 'config.txt').read_text() + '\nboot_ramdisk=1\n')
            import json
            image_plan = json.loads((output / 'profile/image-plan.json').read_text())
            text, _ = recipe(images, image_plan)
            cfg = output / 'profile/genimage.cfg'; cfg.write_text(text)
            empty = output / 'empty'; empty.mkdir()
            run([str(host / 'bin/genimage'), '--rootpath', str(empty),
                 '--tmppath', str(output / 'genimage.tmp'), '--inputpath', str(images),
                 '--outputpath', str(images), '--config', str(cfg)], env=env)
            with (images / 'sdcard.img').open('rb') as disk:
                mbr = disk.read(512)
                assert mbr[510:] == b'\x55\xaa'
                entries = [struct.unpack_from('<B3sB3sII', mbr, 446 + i * 16) for i in range(4)]
                assert [entry[2] for entry in entries] == [0xC, 0x83, 0x83, 0]
                assert [entry[5] * 512 for entry in entries[:3]] == [128 << 20, 120 << 20, 64 << 20]
                disk.seek(entries[2][4] * 512 + 1024 + 56)
                assert disk.read(2) == b'\x53\xef'
                disk.seek(entries[2][4] * 512 + 1024 + 120)
                assert disk.read(16).rstrip(b'\0') == b'polis-data'
                disk.seek(entries[1][4] * 512)
                assert disk.read(len(payload)) == payload
            fat = images / ('boot.img' if image_plan['ramdisk'] else 'boot.vfat')
            kernel = 'Image.lz4boot' if image_plan['lz4'] else 'Image'
            assert run([str(host / 'bin/mtype'), '-i', str(fat), '::Image'], env=env) == (images / kernel).read_bytes()
            start = 'start4cd.elf' if image_plan['cutdown'] else 'start4.elf'
            assert run([str(host / 'bin/mtype'), '-i', str(fat), '::' + start], env=env) == (firmware / start).read_bytes()
            if image_plan['ramdisk']:
                assert run([str(host / 'bin/mtype'), '-i', str(images / 'boot.vfat'), '::boot.img'], env=env) == fat.read_bytes()
            print(f'PASS: {name}: resolved config, FAT payload, root payload, ext4 data, partition layout')


if __name__ == '__main__':
    main()
