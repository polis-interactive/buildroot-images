"""Prepare per-card host/AP names in a disposable data filesystem."""
from pathlib import Path
import re
import shutil
import subprocess

from ..common import Context, UserError, run


def validate(name: str) -> str:
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', name):
        raise UserError('--hostname must be 1–63 lowercase letters, digits or hyphens, '
                        'starting and ending with a letter or digit (omit .local).')
    return name


def validate_ap_name(name: str) -> str:
    # This subset is both an SSID and a safe literal iwd AP profile filename.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', name):
        raise UserError('--ap-name must be 1–32 ASCII letters, digits, hyphens or '
                        'underscores, starting with a letter or digit.')
    return name


def check_hostname_support(ctx: Context, image: Path, layout: dict) -> None:
    dump = shutil.which('dump.erofs')
    if dump is None:
        candidate = ctx.repo / '.output/host/bin/dump.erofs'
        if candidate.is_file():
            dump = str(candidate)
    if dump is None:
        raise UserError('--hostname requires host dump.erofs (erofs-utils), '
                        'on PATH or in .output/host/bin/.')
    root = layout['partitions'][1]
    try:
        def read(path):
            return run([dump, f'--offset={root["start"] * 512}', '--cat',
                        f'--path={path}', image], capture=True).stdout
        if ('/etc/init.d/polis-hostname' not in read('/etc/init.d/mount-data')
                or '# polis-hostname-v1:' not in read('/etc/init.d/polis-hostname')):
            raise ValueError('missing startup hook')
    except (ValueError, subprocess.CalledProcessError) as error:
        raise UserError('This image does not support --hostname. Regenerate the image '
                        'with the updated startup scripts once, then reuse it for every Pi.') from error


def prepare(ctx: Context, image: Path, layout: dict, name: str | None, work: Path,
            *, ap_name: str | None = None) -> Path:
    """Check support and edit only a temporary copy, before disk confirmation."""
    if name is not None:
        validate(name)
    if ap_name is not None:
        validate_ap_name(ap_name)
    missing = [tool for tool in ('debugfs', 'e2fsck') if not shutil.which(tool)]
    if missing:
        raise UserError('Per-card names require these host tools: ' + ', '.join(missing))
    if name is not None:
        check_hostname_support(ctx, image, layout)
    part = layout['partitions'][2]
    data = work / 'data.ext4'
    with image.open('rb') as source, data.open('wb') as target:
        source.seek(part['start'] * 512)
        remaining = part['size'] * 512
        while remaining:
            block = source.read(min(remaining, 1024 * 1024))
            if not block:
                raise UserError('Truncated data partition; no card written.')
            target.write(block)
            remaining -= len(block)
    run(['e2fsck', '-f', '-n', data], capture=True)
    if name is not None:
        set_hostname(data, name, work)
    if ap_name is not None:
        set_ap_name(data, ap_name, work)
    run(['e2fsck', '-f', '-n', data], capture=True)
    return data


def set_hostname(data: Path, name: str, work: Path) -> None:
    payload = work / 'hostname'
    payload.write_text(name + '\n')
    payload.chmod(0o644)
    # Never mkdir an existing directory: some debugfs versions allocate an
    # orphan inode even though they report the name collision as an error.
    config = run(['debugfs', '-R', 'stat /config', data], capture=True).stdout
    if not config:
        run(['debugfs', '-w', '-R', 'mkdir /config', data], capture=True)
        config = run(['debugfs', '-R', 'stat /config', data], capture=True).stdout
    if not re.search(r'Type:\s+directory\b', config):
        raise UserError('Expected /data/config to be a directory; no card written.')
    existing = run(['debugfs', '-R', 'stat /config/hostname', data], capture=True).stdout
    commands = []
    if existing:
        if not re.search(r'Type:\s+regular\b', existing):
            raise UserError('Expected /data/config/hostname to be a regular file; no card written.')
        commands.append('rm /config/hostname')
    commands.append('write hostname /config/hostname')
    # debugfs can return success even on command errors. Verify content and fsck
    # after the edits; never touch the source snapshot or mount a host filesystem.
    for command in commands:
        run(['debugfs', '-w', '-R', command, data], capture=True, cwd=work)
    result = run(['debugfs', '-R', 'cat /config/hostname', data], capture=True)
    if result.stdout != name + '\n':
        raise UserError('Failed to prepare /data/config/hostname; no card written.')


def set_ap_name(data: Path, name: str, work: Path) -> None:
    def stat(path):
        return run(['debugfs', '-R', f'stat {path}', data], capture=True).stdout

    def read(path):
        if not re.search(r'Type:\s+regular\b', stat(path)):
            raise UserError(f'--ap-name requires an initialized Wi-Fi image: missing regular {path}.')
        return run(['debugfs', '-R', f'cat {path}', data], capture=True).stdout

    previous = read('/wifi-setupd/ssid').rstrip('\n')
    validate_ap_name(previous)
    old_profile = f'/var-lib/iwd/ap/{previous}.ap'
    new_profile = f'/var-lib/iwd/ap/{name}.ap'
    profile = read(old_profile)
    if not profile.strip() or not read('/wifi-setupd/device-key').strip():
        raise UserError('--ap-name requires a nonempty AP profile and device key.')
    if previous == name:
        return
    if stat(new_profile):
        raise UserError(f'AP profile {new_profile} already exists; no card written.')
    # Keep the password, network settings, and unrelated iwd profiles unchanged.
    # New credential-bearing files remain root-owned and mode 0600.
    for filename, content, destination in (
            ('ap-profile', profile, new_profile),
            ('ap-ssid', name + '\n', '/wifi-setupd/ssid')):
        payload = work / filename
        payload.touch(mode=0o600, exist_ok=True)
        payload.chmod(0o600)
        payload.write_text(content)
        if destination == '/wifi-setupd/ssid':
            run(['debugfs', '-w', '-R', f'rm {destination}', data], capture=True)
        run(['debugfs', '-w', '-R', f'write {filename} {destination}', data], capture=True, cwd=work)
        for field, value in (('uid', '0'), ('gid', '0'), ('mode', '0100600')):
            run(['debugfs', '-w', '-R', f'set_inode_field {destination} {field} {value}', data], capture=True)
        if read(destination) != content:
            raise UserError('Failed to verify AP name configuration; no card written.')
    run(['debugfs', '-w', '-R', f'rm {old_profile}', data], capture=True)
    if stat(old_profile):
        raise UserError('Failed to remove the previous AP profile; no card written.')
