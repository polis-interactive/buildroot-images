"""Bounded, read-only retrieval of this boot's immutable Pi diagnostic capture."""
import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import sys
import tempfile
import termios
import time
import tty

from ..diagnostics.bootbench import BOOT_ID, import_capture
from ..common import UserError
from .usb import discover

MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
NAMES = {'boot.log', 'gpio.log', 'cmdline.txt', 'mountinfo.txt', 'uptime.txt', 'kernel-version.txt'}
for stem in ('dmesg', 'firmware', 'firmware-version', 'bootloader-config',
             'bootloader-version', 'firmware-config-int', 'firmware-config-str'):
    NAMES.add(stem + ('.log' if stem in ('dmesg', 'firmware') else '.txt'))
    NAMES.add(stem + '-error.txt')


class Wire:
    def __init__(self, fd, deadline):
        self.fd, self.deadline, self.buffer = fd, deadline, b''

    def wait(self, write=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise UserError('Pi diagnostic retrieval deadline exceeded; no complete capture saved.')
        r, w, _ = select.select([] if write else [self.fd], [self.fd] if write else [], [], remaining)
        if not (r or w):
            raise UserError('Pi diagnostic retrieval deadline exceeded; no complete capture saved.')

    def send(self, data):
        while data:
            self.wait(write=True)
            n = os.write(self.fd, data)
            if not n:
                raise UserError('Pi USB disconnected during request.')
            data = data[n:]

    def line(self):
        while b'\n' not in self.buffer:
            self.wait()
            data = os.read(self.fd, 4096)
            if not data:
                raise UserError('Pi USB disconnected during capture.')
            self.buffer += data
            if len(self.buffer.split(b'\n', 1)[0]) > 4096:
                raise UserError('Oversized diagnostic line.')
        line, self.buffer = self.buffer.split(b'\n', 1)
        if len(line) > 4096:
            raise UserError('Oversized diagnostic line.')
        try:
            return line.decode('ascii').rstrip('\r')
        except UnicodeDecodeError as error:
            raise UserError('Non-ASCII diagnostic framing.') from error


@contextmanager
def connection(port, deadline):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    previous = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = termios.tcgetattr(fd)
        tty.setraw(fd, termios.TCSANOW)
        attrs = termios.tcgetattr(fd)
        attrs[2] = (attrs[2] | termios.CLOCAL | termios.CREAD) & ~termios.CRTSCTS
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        yield Wire(fd, deadline)
    finally:
        if previous is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, previous)
            except termios.error:
                pass
        os.close(fd)


def receive(wire, nonce, boot_id, archive):
    """Accept only a complete, hashed capture for the boot ID just queried."""
    with tempfile.TemporaryDirectory(prefix='polis-capture-') as temp:
        source = Path(temp) / boot_id
        source.mkdir()
        total, names = 0, set()
        while True:
            line = wire.line()
            if line == f'POLIS_CAPTURE_END {nonce} {boot_id}':
                if not names.intersection({'boot.log', 'gpio.log'}):
                    raise UserError('Incomplete diagnostic capture: missing boot.log.')
                destination = import_capture(source, archive, None)
                return dict(boot_id=boot_id, path=str(destination),
                            capture=json.loads((destination / 'capture.json').read_text()))
            fields = line.split()
            if len(fields) != 4 or fields[0] != 'FILE':
                raise UserError('Invalid diagnostic file header or mismatched capture trailer.')
            _, name, size, digest = fields
            if name not in NAMES or name in names or not size.isdecimal() or not re.fullmatch('[0-9a-f]{64}', digest):
                raise UserError('Invalid/duplicate diagnostic filename, size, or digest.')
            size = int(size)
            total += size
            if size > MAX_FILE or total > MAX_TOTAL:
                raise UserError('Diagnostic capture exceeds size limit.')
            encoded = bytearray()
            while (line := wire.line()) != 'END_FILE':
                encoded.extend(line.encode('ascii'))
                if len(encoded) > 4 * ((size + 2) // 3):
                    raise UserError('Diagnostic file exceeds declared size.')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise UserError('Invalid diagnostic base64.') from error
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise UserError('Diagnostic size/SHA-256 verification failed.')
            (source / name).write_bytes(data)
            names.add(name)


def capture(port, archive, timeout=30):
    deadline = time.monotonic() + timeout
    with connection(port, deadline) as wire:
        # Retry only read-only pings until init opens the gadget. Never resend
        # a capture mid-stream: a lost/truncated transfer fails as one attempt.
        next_ping = 0
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise UserError('Pi diagnostic retrieval deadline exceeded during startup.')
            if now >= next_ping:
                wire.send(b'POLIS_PING\n')
                next_ping = now + .25
            wait = max(0, min(deadline, next_ping) - time.monotonic())
            if not wire.buffer and not select.select([wire.fd], [], [], wait)[0]:
                continue
            line = wire.line()
            # Before init configures ttyGS0 raw, the gadget may echo the host's
            # probe. The banner can also precede its first response. Neither
            # proves an old image; keep the bounded read-only handshake going.
            if line in ('', 'POLIS_USB_READY', 'POLIS_PING'):
                continue
            match = re.fullmatch(r'POLIS_USB_OK uptime=\S+ boot_id=(\S+)', line)
            if match and BOOT_ID.fullmatch(match[1]):
                boot_id = match[1]
                break
            raise UserError(f'Pi image lacks capture protocol v1 or returned invalid startup framing: {line[:160]!r}')
        nonce = secrets.token_hex(16)
        while True:
            wire.send(f'POLIS_CAPTURE {nonce}\n'.encode())
            line = wire.line()
            while line == 'POLIS_USB_READY' or re.fullmatch(r'POLIS_USB_OK uptime=\S+ boot_id=' + re.escape(boot_id), line):
                line = wire.line()
            if line == f'POLIS_CAPTURE_BUSY {nonce}':
                time.sleep(min(.25, max(0, deadline-time.monotonic())))
                continue
            if line != f'POLIS_CAPTURE_BEGIN {nonce} {boot_id}':
                raise UserError('Capture boot ID/request mismatch; Pi rebooted or protocol changed.')
            return receive(wire, nonce, boot_id, archive)


def main():
    try:
        request = json.load(sys.stdin)
        deadline = time.monotonic() + 30
        while True:
            try:
                port = discover(usb_path=request['usb_path'])
                break
            except UserError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.25)
        data = capture(port, Path(request['archive']), max(.01, deadline - time.monotonic()))
        print(json.dumps(dict(ok=True, data=data)))
    except (OSError, ValueError, KeyError, UserError, termios.error) as error:
        print(json.dumps(dict(ok=False, error=str(error))))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
