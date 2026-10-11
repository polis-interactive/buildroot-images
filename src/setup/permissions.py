"""Install the tracked host access policy; never select or operate hardware."""
import argparse
import grp
import hashlib
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

from ..common import UserError, run

GROUP = 'polis-wired'
RULE_NAME = '70-polis-wired.rules'
RULE_SOURCE = Path(__file__).resolve().parents[2] / 'package/polis-wired-control/udev' / RULE_NAME
RULES_DIRECTORY = Path('/etc/udev/rules.d')
REPLACED_RULES = ('70-polis-rpiboot.rules', '70-polis-recovery-storage.rules', '99-polis-tester.rules')


def validate(source):
    # Syntax-only verification, with no device events or root privileges needed.
    run(['udevadm', 'verify', '--resolve-names=late', source])


def backups_for(directory, data):
    backups = []
    for name in (*REPLACED_RULES, RULE_NAME):
        old = directory / name
        if old.is_symlink():
            raise UserError(f'{old}: managed by a symlink; resolve it before installing permissions')
        if not old.exists():
            continue
        contents = old.read_bytes()
        if name == RULE_NAME and contents == data:
            continue
        digest = hashlib.sha256(contents).hexdigest()
        backup = directory / f'{name}.{digest}.disabled'
        if backup.is_symlink() or (backup.exists() and backup.read_bytes() != contents):
            raise UserError(f'{backup}: backup conflict; preserve it before retrying')
        backups.append((old, backup))
    return backups


def write_rules(directory, data):
    directory.mkdir(parents=True, exist_ok=True)
    backups = backups_for(directory, data)
    # Preserve the active policy until the replacement is ready to publish.
    with tempfile.NamedTemporaryFile(prefix='.polis-rules-', dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(0o644)
        for old, backup in backups:
            shutil.copy2(old, backup)
            print(f'Previous rule saved: {backup}')
        temporary.replace(directory / RULE_NAME)
        for old, _ in backups:
            if old.name != RULE_NAME:
                old.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def install(user):
    if os.geteuid() != 0:
        raise UserError('Installing host permissions requires root; use make setup-permissions')
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        raise UserError(f'No local user {user!r}') from None
    if account.pw_uid == 0:
        raise UserError('Select the non-root daemon user with WIRED_USER=<name>')
    missing = [tool for tool in ('udevadm', 'groupadd', 'usermod') if not shutil.which(tool)]
    if missing:
        raise UserError('Permission setup requires: ' + ', '.join(missing))
    validate(RULE_SOURCE)
    data = RULE_SOURCE.read_bytes()
    # Check migration conflicts before editing group membership or installed files.
    backups_for(RULES_DIRECTORY, data)
    try:
        group = grp.getgrnam(GROUP)
    except KeyError:
        run(['groupadd', '--system', GROUP])
        group = grp.getgrnam(GROUP)
    if account.pw_gid != group.gr_gid and user not in group.gr_mem:
        run(['usermod', '--append', '--groups', GROUP, '--', user])
    write_rules(RULES_DIRECTORY, data)
    run(['udevadm', 'control', '--reload-rules'])
    print(f'Installed {RULE_NAME}; {user} is a member of {GROUP}.')
    print('Log out and back in, then restart the daemon. Reconnect devices while the fixture is idle '
          'to apply the new rules. No device events were triggered.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', help='non-root account running the daemon')
    parser.add_argument('--check', action='store_true', help='verify tracked rule syntax without installing')
    args = parser.parse_args(argv)
    try:
        if args.check:
            validate(RULE_SOURCE)
        else:
            if not args.user:
                raise UserError('--user is required when installing permissions')
            install(args.user)
    except (UserError, OSError, subprocess.CalledProcessError) as error:
        print(f'Permission setup failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
