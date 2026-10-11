"""Explicit Pi recovery storage adapter. No device actions occur on import.

Called by polis-wired-daemon with one JSON request on stdin. The interactive
images.flash entry point is deliberately not bypassed or modified; its reusable
validation functions are shared with this separately authorized workflow.
"""
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tarfile

from ..common import Context, UserError
from ..artifacts.images import discover_images, disks, inspect_destination

CHUNK = 1024 * 1024


def digest_file(stream, length=None):
    digest = hashlib.sha256()
    remaining = length
    while remaining is None or remaining:
        data = stream.read(CHUNK if remaining is None else min(CHUNK, remaining))
        if not data:
            if remaining:
                raise UserError("Short read during verification")
            break
        digest.update(data)
        if remaining is not None:
            remaining -= len(data)
    return digest.hexdigest()


def payload_hash(directory):
    root = Path(directory).resolve(strict=True)
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob('*') if p.is_file())
    if not files:
        raise UserError('Empty recovery payload')
    for path in files:
        digest.update(str(path.relative_to(root)).encode() + b'\0')
        with path.open('rb') as stream:
            digest.update(bytes.fromhex(digest_file(stream)))
    return digest.hexdigest()


def validate_payload(config):
    root = Path(config['gadget_dir'])
    if payload_hash(root) != config['gadget_sha256'].lower():
        raise UserError('Recovery payload hash mismatch')
    settings = (root / 'config.txt').read_text()
    active = '\n'.join(line.split('#', 1)[0].strip() for line in settings.splitlines())
    if 'boot_ramdisk=1' not in active.splitlines() or 'program_' in active:
        raise UserError('Expected RAM mass-storage config, not OTP provisioning')
    if any((root / name).exists() for name in ('recovery.bin', 'pieeprom.bin', 'pieeprom.upd')):
        raise UserError('EEPROM recovery/provisioning payloads are forbidden')
    if not (root / 'boot.img').is_file():
        raise UserError('Expected RAM mass-storage payload with boot.img')
    archive = root / 'bootfiles.bin'
    if archive.is_file():
        # Current official rpiboot reads 2711/bootcode4.bin from this tar
        # archive. Inspect in place; never extract executable recovery files.
        try:
            with tarfile.open(archive, 'r:') as files:
                members = files.getmembers()
                loaders = [m for m in members if m.name == '2711/bootcode4.bin']
                if len(loaders) != 1 or not loaders[0].isfile() or not loaders[0].size:
                    raise UserError('Expected Pi 4 loader in bootfiles.bin')
                if any(Path(m.name).name in ('recovery.bin', 'pieeprom.bin', 'pieeprom.upd')
                       for m in members):
                    raise UserError('EEPROM recovery/provisioning payloads are forbidden')
        except (tarfile.TarError, OSError) as error:
            raise UserError(f'Invalid bootfiles.bin archive: {error}') from error
    elif not (root / 'bootcode4.bin').is_file():
        raise UserError('Expected Pi 4 bootfiles.bin archive or unpacked bootcode4.bin')


def image_open(image):
    path = Path(image['path']).resolve(strict=True)
    if (path.parent.parent / '.incomplete').exists():
        raise UserError('Incomplete snapshot')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    stream = os.fdopen(fd, 'rb')
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_size % 512:
            raise UserError('Expected nonempty, sector-aligned raw image')
        if digest_file(stream) != image['sha256'].lower():
            raise UserError('Source image SHA-256 mismatch')
        stream.seek(0)
        return stream, info
    except BaseException:
        stream.close()
        raise


def properties(device):
    result = subprocess.run(['udevadm', 'info', '--query=property', '--name', str(device)],
                            check=True, capture_output=True, text=True, timeout=10)
    return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)


def usb_ancestry(device):
    try:
        sysdev = (Path('/sys/class/block') / device.name / 'device').resolve(strict=True)
    except FileNotFoundError:
        # Virtual disks (e.g. zram) have no physical device ancestry. A device
        # disappearing during enumeration is likewise never a candidate.
        return []
    return [p.name for p in (sysdev, *sysdev.parents)]


def matching_disk(config):
    matches = []
    for node in disks():
        if node['type'] != 'disk':
            continue
        device = Path(node['name']).resolve(strict=True)
        if config['usb_path'] not in usb_ancestry(device):
            continue
        props = properties(device)
        if (props.get('ID_BUS') == 'usb' and props.get('ID_PATH') == config['disk_id_path']
                and props.get('ID_SERIAL_SHORT') == config['disk_serial']):
            matches.append(device)
    if len(matches) > 1:
        raise UserError('Ambiguous recovery disk identity; no disk selected')
    return matches[0] if matches else None


def reject_holders(device):
    root = Path('/sys/class/block') / device.name
    candidates = [root, *(p for p in root.iterdir() if (p / 'partition').exists())]
    for node in candidates:
        holders = node / 'holders'
        if holders.exists() and any(holders.iterdir()):
            raise UserError('Destination or partition has active device-mapper/RAID holders')


def checked_disk(config, size):
    device = matching_disk(config)
    if device is None:
        raise UserError('Configured recovery SD LUN not present')
    identity = inspect_destination(device, size)
    reject_holders(device)
    return device, identity


def direct_digest(fd, size):
    """Aligned Linux O_DIRECT readback; never fall back to the host page cache."""
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_DIRECT)
        if not fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_DIRECT:
            raise UserError('Direct readback unavailable; verification refused')
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        remaining = size
        # mmap supplies a page-aligned buffer; ordinary Python bytes do not.
        with mmap.mmap(-1, CHUNK) as buffer:
            while remaining:
                length = min(CHUNK, remaining)
                with memoryview(buffer)[:length] as view:
                    count = os.readv(fd, [view])
                    if count != length:
                        raise UserError('Short direct read during verification')
                    digest.update(view)
                remaining -= length
        return digest.hexdigest()
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)


def copy_verify(source, target, size, expected_hash):
    """Shared byte-copy/readback core; tests use ordinary temporary files only."""
    block = stat.S_ISBLK(os.fstat(target.fileno()).st_mode)
    if block:
        sector = struct.unpack('I', fcntl.ioctl(target.fileno(), 0x1268, bytes(4)))[0]
        if not sector or size % sector or CHUNK % sector:
            raise UserError('Image/readback size is not aligned to the destination sector size')
        # Confirm direct I/O works before changing any bytes on the device.
        direct_digest(target.fileno(), sector)
        target.seek(0)
    remaining = size
    while remaining:
        data = source.read(min(CHUNK, remaining))
        if not data:
            raise UserError('Source truncated during flash; outcome uncertain')
        view = memoryview(data)
        while view:
            written = target.write(view)
            if not written:
                raise UserError('Short write; outcome uncertain')
            view = view[written:]
        remaining -= len(data)
    target.flush()
    os.fsync(target.fileno())
    if block:
        actual_hash = direct_digest(target.fileno(), size)
    else:
        target.seek(0)
        actual_hash = digest_file(target, size)
    if actual_hash != expected_hash.lower():
        raise UserError('Read-back SHA-256 mismatch; do not boot this image')


def flash(config, image, confirmation):
    if confirmation != config['fixture']:
        raise UserError('Explicit fixture confirmation required')
    source, before = image_open(image)
    with source:
        device, identity = checked_disk(config, before.st_size)
        fd = os.open(device, os.O_RDWR | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r+b', buffering=0) as target:
            if os.fstat(fd).st_rdev != identity[0]:
                raise UserError('Destination identity changed during open')
            if checked_disk(config, before.st_size) != (device, identity):
                raise UserError('Destination changed before write')
            copy_verify(source, target, before.st_size, image['sha256'])
            after = os.fstat(source.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise UserError('Source changed during flash')
            if checked_disk(config, before.st_size) != (device, identity):
                raise UserError('Destination changed during verification')
    return {'device': str(device), 'bytes_verified': before.st_size, 'sha256': image['sha256']}


def dispatch(request):
    config = request['config']
    action = request['action']
    if action == 'payload':
        validate_payload(config)
        return {'validated': True}
    if action == 'preflight':
        validate_payload(config)
        if not config['otp_gpio8_provisioned']:
            raise UserError('Fixture GPIO8 OTP provisioning has not been qualified')
        for image in request.get('images', []):
            stream, _ = image_open(image)
            stream.close()
        return {'validated': True}
    if action == 'probe':
        device = matching_disk(config)
        return None if device is None else str(device)
    if action == 'rom':
        root = Path('/sys/bus/usb/devices') / config['usb_path']
        try:
            return (root / 'idVendor').read_text().strip() == '0a5c' and (root / 'idProduct').read_text().strip() == '2711'
        except FileNotFoundError:
            return False
    if action == 'flash':
        return flash(config, request['image'], request.get('confirm_fixture'))
    if action == 'images':
        root = Path(config['repo_root'])
        context = Context(root, root / 'buildroot', root / 'dl', root / 'snapshots')
        return [str(p) for p in discover_images(context)]
    raise UserError('Unknown storage adapter action')


def main():
    if len(sys.argv) == 3 and sys.argv[1] == 'payload-hash':
        print(payload_hash(sys.argv[2]))
        return
    try:
        request = json.load(sys.stdin)
        print(json.dumps({'ok': True, 'data': dispatch(request)}))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        sys.exit(1)


if __name__ == '__main__':
    main()
