"""Local SSH identity for the development profile."""
import base64
from pathlib import Path
import struct
import subprocess

from ..common import UserError, run


def public_key(path):
    """Accept one public key, never private material or authorized_keys options."""
    try:
        lines = [line.strip() for line in Path(path).read_text().splitlines()
                 if line.strip() and not line.startswith('#')]
        if len(lines) != 1:
            raise ValueError('expected one public key')
        kind, encoded, *_ = lines[0].split()
        blob = base64.b64decode(encoded, validate=True)
        length = struct.unpack('>I', blob[:4])[0]
        if kind not in ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256',
                        'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521') or blob[4:4+length].decode() != kind:
            raise ValueError('invalid SSH public key type')
        run(['ssh-keygen', '-l', '-f', path], capture=True)
        # Comments are not image inputs and may contain identifying host paths.
        return f'{kind} {encoded}\n'
    except (OSError, ValueError, struct.error, subprocess.CalledProcessError) as error:
        raise UserError(f'{path}: expected an SSH public-key file: {error}') from error


def ensure_key(repo, explicit=None):
    if explicit is not None:
        public_key(explicit)
        return Path(explicit)
    directory = repo / '.local/development-ssh'
    private = directory / 'id_ed25519'
    public = directory / 'id_ed25519.pub'
    if not public.exists():
        if private.exists():
            raise UserError(f'{private}: private key exists without its .pub; restore the public key before building')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'polis-development', '-f', private])
        print(f'Local development SSH identity: {private} (only the public key enters the image)')
    public_key(public)
    return public
