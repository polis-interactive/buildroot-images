"""Assemble an image with a declared first-boot policy; no pre-staged EEPROM writes."""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile


def recipe(images, plan):
    kernel = 'Image.lz4boot' if plan['lz4'] else 'Image'
    dtb = 'bcm2711-rpi-4-b.premerged.dtb' if plan['premerge'] else 'bcm2711-rpi-4-b.dtb'
    start, fixup = ('start4cd.elf', 'fixup4cd.dat') if plan['cutdown'] else ('start4.elf', 'fixup4.dat')
    files = {'bcm2711-rpi-4-b.dtb': dtb, 'config.txt': 'rpi-firmware/config.txt',
             'cmdline.txt': 'rpi-firmware/cmdline.txt', start: 'rpi-firmware/' + start,
             fixup: 'rpi-firmware/' + fixup, 'Image': kernel}
    if not plan['premerge']:
        files['overlays'] = 'rpi-firmware/overlays'

    def fat(name, files, size, compact=False):
        lines = [f'image {name} {{', '\tvfat {']
        if compact:
            lines.append('\t\textraargs = "-F 16 -s 1"')
        for destination, source in files.items():
            lines.append(f'\t\tfile {destination} {{ image = "{source}" }}')
        return '\n'.join([*lines, '\t}', f'\tsize = {size}M', '}'])

    inner_size = None
    if plan['ramdisk']:
        # FAT16 uses two 2-byte FAT entries per 512-byte cluster. Reserve
        # another 64 KiB for root/subdirectories.
        total = 0
        for source in files.values():
            path = images / source
            paths = path.rglob('*') if path.is_dir() else [path]
            total += sum(p.stat().st_size + 512 for p in paths if p.is_file())
        inner_size = max(4, math.ceil((total * 512 / 508 + 64 * 1024) / (2 * 1024**2)) * 2)
        if inner_size > 32:
            raise ValueError('Selected boot payload exceeds the supported 32 MiB FAT16 ramdisk; disable boot-ramdisk')
        text = fat('boot.img', files, inner_size, True) + '\n\n'
        text += fat('boot.vfat', {'config.txt': 'boot-ramdisk-config.txt', 'boot.img': 'boot.img'}, 128)
    else:
        text = fat('boot.vfat', files, 128)
    text += '''

image data.ext4 {
    ext4 { use-mke2fs = true
           label = "polis-data" }
    size = 64M
    empty = true
}
image sdcard.img {
    hdimage { }
    partition boot {
        partition-type = 0xC
        bootable = "true"
        image = "boot.vfat"
    }
    partition rootfs {
        partition-type = 0x83
        size = 120M
        image = "rootfs.erofs"
    }
    partition data {
        partition-type = 0x83
        image = "data.ext4"
    }
}
'''
    return text, inner_size


def assemble(env=None):
    env = dict(os.environ if env is None else env)
    output = Path(env['BASE_DIR'])
    repo = Path(env['BR2_EXTERNAL_POLIS_PATH'])
    images, host, build = [Path(env[k]) for k in ('BINARIES_DIR', 'HOST_DIR', 'BUILD_DIR')]
    plan = json.loads((output / 'profile/image-plan.json').read_text())
    if plan['lz4']:
        subprocess.run(['python3', str(repo / 'board/rpi4/lz4-loader/build.py'),
                        str(images / 'Image'), str(images / 'Image.lz4boot'), str(host),
                        str(build / 'polis-lz4-loader')], check=True, env=env)
    if plan['premerge']:
        subprocess.run(['python3', str(repo / 'board/rpi4/premerge-dtb.py'), str(images),
                        str(images / 'bcm2711-rpi-4-b.premerged.dtb'), str(build), str(host)], check=True, env=env)
    if plan['ramdisk']:
        config = (images / 'rpi-firmware/config.txt').read_text()
        (images / 'boot-ramdisk-config.txt').write_text(config.rstrip() + '\nboot_ramdisk=1\n')
    text, size = recipe(images, plan)
    config = output / 'profile/genimage.cfg'
    config.write_text(text)
    env['PATH'] = str(host / 'bin') + ':' + str(host / 'sbin') + ':' + env.get('PATH', '')
    with tempfile.TemporaryDirectory(prefix='polis-genimage-root-') as empty:
        subprocess.run([str(host / 'bin/genimage'), '--rootpath', empty,
                        '--tmppath', str(build / 'genimage.tmp'), '--inputpath', str(images),
                        '--outputpath', str(images), '--config', str(config)], check=True, env=env)
    provisioning = dict(schema=1, profile=plan['profile'], plan_sha256=plan['fingerprint'],
                        enabled=not plan['development'], settings=plan['eeprom'],
                        mode='development' if plan['development'] else 'final',
                        expand_data=not plan['development'],
                        status='disabled' if plan['development'] else 'pending-first-boot',
                        scope='first-boot',
                        note='Target verifies EEPROM settings and expands /data. Development mode does not revert existing board settings.')
    (images / 'provisioning.json').write_text(json.dumps(provisioning, indent=2) + '\n')
    (output / 'profile/image-result.json').write_text(json.dumps(dict(boot_ramdisk_mib=size), indent=2) + '\n')


if __name__ == '__main__':
    assemble()
