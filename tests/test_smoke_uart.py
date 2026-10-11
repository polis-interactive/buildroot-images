"""Run the boot marker script against a pseudo-UART, never physical hardware."""
from pathlib import Path
import os
import pty
import select
import shlex
import subprocess
import sys
import tempfile
import termios
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'board/rpi4/smoke-init-finished.sh'


class SmokeUartTest(unittest.TestCase):
    def run_probe(self, cmdline, init_status=0):
        master, slave = pty.openpty()
        try:
            tty = os.ttyname(slave)
            attrs = termios.tcgetattr(slave)
            attrs[4] = attrs[5] = termios.B9600
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'cmdline').write_text(cmdline)
                (root / 'uptime').write_text('2.50 1.00\n')
                # Observe the real pseudo-TTY speed when init begins, rather
                # than merely checking the spelling of an stty command.
                observer = root / 'baud.py'
                observer.write_text(
                    'import os, termios\n'
                    f'fd = os.open({tty!r}, os.O_RDONLY | os.O_NOCTTY)\n'
                    'speed = termios.tcgetattr(fd)[5]\n'
                    'print({termios.B9600: 9600, termios.B115200: 115200}[speed])\n'
                    'os.close(fd)\n'
                )
                init = root / 'rcS'
                init.write_text(f'#!/bin/sh\n{shlex.quote(sys.executable)} {observer} > {root}/init-baud\nexit {init_status}\n')
                init.chmod(0o700)
                script = SCRIPT.read_text()
                for original, replacement in [
                    ('/proc/cmdline', str(root / 'cmdline')),
                    ('/proc/uptime', str(root / 'uptime')),
                    ('/etc/init.d/rcS', str(init)),
                    ('/dev/ttyAMA0', tty),
                ]:
                    script = script.replace(original, replacement)
                result = subprocess.run(['sh', '-c', script], capture_output=True, timeout=5)
                data = b''
                while select.select([master], [], [], 0.05)[0]:
                    data += os.read(master, 4096)
                baud = (root / 'init-baud').read_text().strip()
                return result, data, baud
        finally:
            os.close(slave)
            os.close(master)

    def test_opt_in_sets_115200_before_init_and_emits_readiness(self):
        result, data, baud = self.run_probe('root=/dev/mmcblk0p2 console=null polis.smoke=1\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(baud, '115200')
        self.assertEqual(data, b'POLIS_SMOKE_READY init uptime=2.50\n')

    def test_normal_boot_preserves_uart_and_still_runs_init(self):
        result, data, baud = self.run_probe('console=null polis.smoke=0\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(baud, '9600')
        self.assertEqual(data, b'')

    def test_failed_init_cannot_emit_success_marker(self):
        result, data, baud = self.run_probe('polis.smoke=1\n', init_status=7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(baud, '115200')
        self.assertEqual(data, b'')
