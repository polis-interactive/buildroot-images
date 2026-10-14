"""Assemble an image with a declared first-boot policy; no pre-staged EEPROM writes."""
import json
import math
import os
from pathlib import Path
import subprocess
import shutil
import tempfile

from .wifi import write_credentials


def recipe(images, plan):
    reproducible = plan.get('reproducible', False)
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
        args = ['--invariant'] if reproducible else []
        if compact:
            args += ['-F 16 -s 1']
        if args:
            lines.append('\t\textraargs = "' + ' '.join(args) + '"')
        for destination, source in files.items():
            lines.append(f'\t\tfile {destination} {{ image = "{source}" }}')
        lines += ['\t}', f'\tsize = {size}M']
        if reproducible:
            # In a boot ramdisk layout, boot.img becomes an input to boot.vfat.
            lines.append('\texec-post = "touch -d @$SOURCE_DATE_EPOCH \\"$IMAGEOUTFILE\\""')
        return '\n'.join([*lines, '}'])

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
    if reproducible:
        text = text.replace('label = "polis-data"',
                            'label = "polis-data"\n'
                            '           extraargs = "-U 706f6c69-732d-4465-9665-6c6f706d656e '
                            '-E hash_seed=706f6c69-732d-4465-9665-6c6f706d656e,lazy_itable_init=0,lazy_journal_init=0"')
    if plan.get('wifi_setup'):
        text = text.replace('empty = true', 'mountpoint = "/data"')
    return text, inner_size


def assemble(env=None):
    env = dict(os.environ if env is None else env)
    output = Path(env['BASE_DIR'])
    repo = Path(env['BR2_EXTERNAL_POLIS_PATH'])
    images, host, build = [Path(env[k]) for k in ('BINARIES_DIR', 'HOST_DIR', 'BUILD_DIR')]
    plan = json.loads((output / 'profile/image-plan.json').read_text())
    if plan.get('reproducible'):
        epoch = int(plan['source_date_epoch'])
        env.update(SOURCE_DATE_EPOCH=str(epoch), E2FSPROGS_FAKE_TIME=str(epoch),
                   E2FSCK_TIME=str(epoch), TZ='UTC')
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
    if plan.get('reproducible'):
        # Include generated LZ4, premerged DTB and ramdisk config payloads.
        # Avoid touching equal mtimes, which can otherwise disturb Make rules.
        for path in sorted(images.rglob('*'), reverse=True):
            if not path.is_symlink() and path.stat().st_mtime != epoch:
                os.utime(path, (epoch, epoch))
    text, size = recipe(images, plan)
    config = output / 'profile/genimage.cfg'
    config.write_text(text)
    env['PATH'] = str(host / 'bin') + ':' + str(host / 'sbin') + ':' + env.get('PATH', '')
    with tempfile.TemporaryDirectory(prefix='polis-genimage-root-') as staging:
        command = [str(host / 'bin/genimage'), '--rootpath', staging,
                        '--tmppath', str(build / 'genimage.tmp'), '--inputpath', str(images),
                        '--outputpath', str(images), '--config', str(config)]
        if plan.get('wifi_setup'):
            data = Path(staging) / 'data'
            # mount-data skips seeding an existing var-lib, so include all the
            # package-provided state, including the persistent D-Bus directory.
            shutil.copytree(output / 'target/var/lib', data / 'var-lib', symlinks=True)
            write_credentials(data, (output / 'profile/wifi-password').read_text())
            if plan.get('reproducible'):
                for path in [*data.rglob('*'), data]:
                    os.utime(path, (epoch, epoch), follow_symlinks=False)
            # Buildroot already uses host-fakeroot to assign root ownership to
            # rootfs content. Use it for the seeded data filesystem as well.
            command = [str(host / 'bin/fakeroot'), '--', 'sh', '-ec',
                       'chown -h -R 0:0 "$1"; shift; exec "$@"', 'sh', str(data), *command]
        subprocess.run(command, check=True, env=env)
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
