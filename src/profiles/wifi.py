"""Host-side inputs and persistent configuration for the Wi-Fi image."""
import os
from pathlib import Path

from ..common import UserError


def validate_password(value):
    value = value.rstrip('\r\n')
    if not 8 <= len(value) <= 63 or not all(33 <= ord(c) <= 126 for c in value):
        raise UserError('Wi-Fi setup password must be 8-63 printable ASCII characters without spaces')
    return value


def password_input(repo):
    value = os.environ.get('POLIS_WIFI_SETUP_PASSWORD')
    if value is None:
        path = Path(repo) / '.local/wifi-setupd/password'
        if not path.is_file():
            return ''
        value = path.read_text()
    return validate_password(value)


def require_password(value):
    if not value:
        raise UserError('Set POLIS_WIFI_SETUP_PASSWORD or create .local/wifi-setupd/password before configuring a Wi-Fi image')
    return validate_password(value)


def write_credentials(data, password, ssid='Polis-Setup'):
    """Write both credentials into a fresh image staging tree, never a live device."""
    password = require_password(password)
    # iwd derives the PSK itself. Buildroot host Python need not have OpenSSL.
    # ELL's settings reader unescapes backslashes in string values.
    passphrase = password.replace('\\', '\\\\')
    files = {
        'wifi-setupd/device-key': password + '\n',
        'wifi-setupd/ssid': ssid + '\n',
        f'var-lib/iwd/ap/{ssid}.ap':
            '[General]\nDisableHT=true\n[Security]\nPassphrase=' + passphrase + '\n'
            '[IPv4]\nAddress=192.168.16.1\nNetmask=255.255.255.240\n'
            'Gateway=192.168.16.1\nDNSList=192.168.16.1\n',
    }
    for relative, content in files.items():
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        with path.open('w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
    (data / 'var-lib/iwd').chmod(0o700)
