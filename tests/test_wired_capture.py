"""USB retrieval uses simulated serial peers; no hardware or storage writes."""
import base64
import hashlib
import os
from pathlib import Path
import pty
import select
import subprocess
import tempfile
import threading
import time
import unittest

from src.common import UserError
from src.wired.capture import capture, receive, Wire

BOOT = '12345678-abcd-1234-abcd-123456789abc'
NONCE = 'a'*32
REPO = Path(__file__).resolve().parents[1]


def file_lines(name='boot.log', data=b'POLIS_BOOT_DIAGNOSTICS_START uptime=1.25\n'):
    return [f'FILE {name} {len(data)} {hashlib.sha256(data).hexdigest()}',
            base64.b64encode(data).decode(), 'END_FILE']


class Lines:
    def __init__(self, lines): self.lines = iter(lines)
    def line(self):
        try: return next(self.lines)
        except StopIteration: raise UserError('disconnected')


class CaptureTest(unittest.TestCase):
    def test_verified_archive_is_immutable_and_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp)
            lines = file_lines() + [f'POLIS_CAPTURE_END {NONCE} {BOOT}']
            first = receive(Lines(lines), NONCE, BOOT, archive)
            again = receive(Lines(lines), NONCE, BOOT, archive)
            self.assertEqual(first, again)
            self.assertIsNone(first['capture']['metrics']['linux_ready_s'])
            bad = file_lines(data=b'changed') + [f'POLIS_CAPTURE_END {NONCE} {BOOT}']
            with self.assertRaisesRegex(UserError, 'changed'):
                receive(Lines(bad), NONCE, BOOT, archive)

    def test_old_image_gpio_capture_remains_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            lines = file_lines('gpio.log', b'POLIS_GPIO_READY uptime=1.25\n')
            lines += [f'POLIS_CAPTURE_END {NONCE} {BOOT}']
            result = receive(Lines(lines), NONCE, BOOT, Path(temp))
            self.assertEqual(result['capture']['metrics']['linux_ready_s'], 1.25)

    def test_corruption_truncation_traversal_duplicates_and_stale_trailer_fail(self):
        valid = file_lines()
        cases = [
            [valid[0], base64.b64encode(b'x'*28).decode(), 'END_FILE'],
            valid[:2],
            file_lines('../gpio.log'),
            valid + valid,
            valid + [f'POLIS_CAPTURE_END {"b"*32} {BOOT}'],
            [f'POLIS_CAPTURE_END {NONCE} {BOOT}'],
            ['FILE gpio.log 2097153 ' + 'a'*64],
            [valid[0], '!', 'END_FILE'],
        ]
        for lines in cases:
            with self.subTest(lines=lines), tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(UserError):
                    receive(Lines(lines), NONCE, BOOT, Path(temp))
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_client_handles_busy_and_fragmented_capture(self):
        master, slave = pty.openpty()
        failures = []
        def peer():
            try:
                wire = Wire(master, time.monotonic()+5)
                self.assertEqual(wire.line(), 'POLIS_PING')
                # Firmware USB can enumerate with echo/CRLF before init sets raw.
                wire.send(b'POLIS_PING\r\nPOLIS_USB_READY\r\n')
                wire.send(f'POLIS_USB_OK uptime=2.5 boot_id={BOOT}\r\n'.encode())
                request = wire.line()
                nonce = request.split()[1]
                wire.send(f'POLIS_CAPTURE_BUSY {nonce}\n'.encode())
                self.assertEqual(wire.line(), request)
                lines = [f'POLIS_CAPTURE_BEGIN {nonce} {BOOT}', *file_lines(), f'POLIS_CAPTURE_END {nonce} {BOOT}']
                data = ('\n'.join(lines)+'\n').encode()
                for i in range(0,len(data),7): wire.send(data[i:i+7])
            except Exception as e: failures.append(e)
        thread = threading.Thread(target=peer)
        try:
            with tempfile.TemporaryDirectory() as temp:
                thread.start()
                result = capture(Path(os.ttyname(slave)), Path(temp), 4)
                self.assertEqual(result['boot_id'], BOOT)
                thread.join(5)
                self.assertEqual(failures, [])
        finally:
            thread.join(5)
            os.close(master); os.close(slave)

    def test_real_shell_exports_binary_logs_for_current_boot_only(self):
        master, slave = pty.openpty()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root/'id').write_text(BOOT+'\n')
                logs = root/'boot-diagnostics'/BOOT
                logs.mkdir(parents=True)
                data = b'hello\x00\xff\n'
                (logs/'boot.log').write_bytes(data)
                (logs/'firmware.log').write_text('100.000: arm_loader: Starting ARM\n')
                (logs/'not-exported.txt').write_text('not part of protocol')
                (logs/'cmdline.txt').symlink_to('/etc/passwd')
                script = (REPO/'profiles/groups/usb-diagnostics/rootfs-overlay/usr/libexec/polis-usb-diagnostic').read_text()
                script = script.replace('/dev/ttyGS0',os.ttyname(slave)).replace('sleep 1','sleep 0')
                script = script.replace('/proc/sys/kernel/random/boot_id',str(root/'id')).replace('/data/boot-diagnostics',str(root/'boot-diagnostics'))
                process = subprocess.Popen(['sh','-c',script],stderr=subprocess.PIPE)
                try:
                    wire = Wire(master, time.monotonic()+5)
                    self.assertEqual(wire.line(),'POLIS_USB_READY')
                    wire.send(f'POLIS_CAPTURE {NONCE}\n'.encode())
                    self.assertEqual(wire.line(),f'POLIS_CAPTURE_BEGIN {NONCE} {BOOT}')
                    result = receive(wire,NONCE,BOOT,root/'archive')
                    saved = Path(result['path'])
                    self.assertEqual((saved/'boot.log').read_bytes(),data)
                    self.assertFalse((saved/'cmdline.txt').exists())
                    self.assertFalse((saved/'not-exported.txt').exists())
                finally:
                    process.terminate(); process.communicate(timeout=3)
        finally:
            os.close(master);os.close(slave)

    def test_client_deadline_with_silent_peer(self):
        master, slave = pty.openpty()
        try:
            with tempfile.TemporaryDirectory() as temp, self.assertRaisesRegex(UserError,'deadline'):
                capture(Path(os.ttyname(slave)),Path(temp),.05)
        finally:
            os.close(master);os.close(slave)
