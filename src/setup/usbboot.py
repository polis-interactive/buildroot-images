"""Download the commissioned usbboot release and build rpiboot locally."""
import fcntl
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from ..common import UserError, run

URL = 'https://github.com/raspberrypi/usbboot.git'
REVISION = '861a703d8b51f42b76bcdff49da3e0f2766966bf'
EEPROM_REVISION = 'ded1e9332bd996f6adc46e10d22d9ef0298b1882'


def prerequisites():
    missing = [name for name in ('git', 'make', 'cc', 'pkg-config') if not shutil.which(name)]
    if missing:
        raise UserError('usbboot setup requires: ' + ', '.join(missing))
    if subprocess.run(['pkg-config', '--exists', 'libusb-1.0']).returncode:
        raise UserError('usbboot setup requires libusb development files '
                        '(Debian/Ubuntu: libusb-1.0-0-dev)')


def verify(path, revision, eeprom_revision):
    for checkout, expected in ((path, revision), (path / 'rpi-eeprom', eeprom_revision)):
        actual = run(['git', '-C', checkout, 'rev-parse', 'HEAD'], capture=True).stdout.strip()
        if actual != expected:
            raise UserError(f'{checkout}: expected commit {expected}, found {actual}; '
                            'move this checkout aside and rerun make setup')
        if run(['git', '-C', checkout, 'status', '--porcelain', '--untracked-files=normal'],
               capture=True).stdout.strip():
            raise UserError(f'{checkout}: contains local changes; preserve them and remove '
                            'the checkout before rerunning make setup')


def install(repo, *, url=URL, revision=REVISION, eeprom_revision=EEPROM_REVISION):
    prerequisites()
    local = repo / '.local'
    local.mkdir(parents=True, exist_ok=True)
    target = local / 'usbboot'
    with (local / '.usbboot-setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink():
            raise UserError(f'{target}: expected a local checkout, not a symlink')
        if target.exists():
            verify(target, revision, eeprom_revision)
            run(['make', '-C', target, 'rpiboot'])
        else:
            # Interrupted downloads/builds never become the installed checkout.
            with tempfile.TemporaryDirectory(prefix='.usbboot-', dir=local) as temporary:
                checkout = Path(temporary) / 'checkout'
                run(['git', 'init', '--quiet', checkout])
                run(['git', '-C', checkout, 'remote', 'add', 'origin', url])
                run(['git', '-C', checkout, 'fetch', '--depth=1', 'origin', revision])
                run(['git', '-C', checkout, 'checkout', '--detach', 'FETCH_HEAD'])
                run(['git', '-C', checkout, 'submodule', 'update', '--init', '--depth=1', 'rpi-eeprom'])
                verify(checkout, revision, eeprom_revision)
                run(['make', '-C', checkout, 'rpiboot'])
                checkout.rename(target)
    print(f'usbboot ready: {target} ({revision[:12]})')


def main():
    try:
        install(Path(__file__).resolve().parents[2])
    except (UserError, OSError, subprocess.CalledProcessError) as error:
        print(f'Setup failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
