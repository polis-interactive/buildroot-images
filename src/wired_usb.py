"""Non-destructive ping of the base image's direct Pi USB diagnostic endpoint."""
import fcntl
import os
from pathlib import Path
import select
import termios
import time
import tty

from .common import UserError

PING_INTERVAL_S = 0.25


def discover(sys_tty: Path = Path('/sys/class/tty'), usb_path: str | None = None) -> Path:
    matches = []
    for entry in sys_tty.glob('ttyACM*'):
        for parent in (entry / 'device').resolve().parents:
            product = parent / 'product'
            if product.is_file() and product.read_text().strip() == 'Polis_Pi_Diagnostics':
                if usb_path is not None and parent.name != usb_path:
                    continue
                matches.append(Path('/dev') / entry.name)
                break
    if len(matches) != 1:
        raise UserError(
            f'Expected exactly one Polis Pi USB diagnostic device; found {len(matches)}. '
            'Boot the rebuilt polis_* image and connect the Pi USB-C data path. '
            'The Pico is a separate device and is never selected by this test.')
    return matches[0]


def ping(port: Path, timeout: float = 5.0) -> str:
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    previous = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = termios.tcgetattr(fd)
        tty.setraw(fd, termios.TCSANOW)
        attrs = termios.tcgetattr(fd)
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[2] &= ~termios.CRTSCTS
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
        pending = b'POLIS_PING\n'
        received = b''
        deadline = time.monotonic() + timeout
        next_ping = time.monotonic()
        while (remaining := deadline - time.monotonic()) > 0:
            # USB enumerates before init starts the Pi's responder. A packet
            # sent before ttyGS0 is opened may be discarded. Only this harmless
            # diagnostic ping is retried; disconnects still end the test.
            if not pending and time.monotonic() >= next_ping:
                pending = b'POLIS_PING\n'
            wait = remaining if pending else min(remaining, max(0, next_ping - time.monotonic()))
            readable, writable, _ = select.select([fd], [fd] if pending else [], [], wait)
            if writable:
                pending = pending[os.write(fd, pending):]
                if not pending:
                    next_ping = time.monotonic() + PING_INTERVAL_S
            if readable:
                chunk = os.read(fd, 4096)
                if not chunk:
                    raise UserError('Pi USB disconnected before replying.')
                received += chunk
                while b'\n' in received:
                    line, received = received.split(b'\n', 1)
                    if line.startswith(b'POLIS_USB_OK uptime='):
                        return line.decode('ascii').rstrip('\r')
                if len(received) > 4096:
                    raise UserError('Unexpected oversized Pi USB diagnostic response.')
        raise UserError(f'Pi USB enumerated at {port}, but did not answer within {timeout:g} seconds.')
    finally:
        if previous is not None:
            try:
                termios.tcsetattr(fd, termios.TCSANOW, previous)
            except termios.error:
                pass  # A physical disconnect can prevent restoring the terminal.
        os.close(fd)


def main() -> int:
    try:
        port = discover()
        print(f'Pi USB device: {port}', flush=True)
        print(ping(port))
        print('PASS: laptop -> Pi -> laptop USB data exchange.')
        return 0
    except (OSError, UserError, termios.error) as error:
        print(f'USB test failed: {error}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
