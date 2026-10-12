"""Recreate the commissioned RAM recovery payload without accessing hardware."""
import fcntl
from pathlib import Path
import shutil
import sys
import tempfile

from ..common import UserError
from ..sbc_controller.storage import validate_payload


def install(repo):
    source = repo / 'package/polis-sbc-controller/recovery'
    upstream = repo / 'local/usbboot/mass-storage-gadget64'
    expected = (source / 'payload.sha256').read_text().strip()
    tools = repo / 'local/polis-sbc-controller-tools'
    tools.mkdir(parents=True, exist_ok=True)
    target = tools / 'gadget'
    with (tools / '.setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_symlink():
            raise UserError(f'{target}: expected a directory, not a symlink')
        if target.exists():
            # Preserve edited payloads; never replace files a running loader may use.
            validate_payload({'gadget_dir': target, 'gadget_sha256': expected})
        else:
            with tempfile.TemporaryDirectory(prefix='.gadget-', dir=tools) as temporary:
                staging = Path(temporary) / 'gadget'
                staging.mkdir()
                shutil.copyfile(source / 'config.txt', staging / 'config.txt')
                for name in ('boot.img', 'bootfiles.bin'):
                    # Dereference upstream's bootfiles.bin symlink.
                    shutil.copyfile(upstream / name, staging / name)
                validate_payload({'gadget_dir': staging, 'gadget_sha256': expected})
                staging.rename(target)
    print(f'Recovery payload ready: {target} ({expected})')


def main():
    try:
        install(Path(__file__).resolve().parents[2])
    except (UserError, OSError) as error:
        print(f'Recovery setup failed: {error}. Run make setup; preserve any edited payload first.',
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
