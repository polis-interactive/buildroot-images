"""USB diagnostics over pseudo-terminals; no physical devices or power changes."""
import importlib.util
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest

from src.common import UserError, read_config
from src.wired_usb import discover, ping


REPO = Path(__file__).resolve().parents[1]
BOARD = REPO / 'board/polis/rpi4'


class WiredUsbTest(unittest.TestCase):
    def test_ping_accepts_fragmented_reply_and_ignores_banner(self):
        master, slave = pty.openpty()
        requests = []

        def peer():
            if select.select([master], [], [], 2)[0]:
                requests.append(os.read(master, 4096))
                os.write(master, b'POLIS_USB_READY\nPOLIS_USB_')
                os.write(master, b'OK uptime=3.25\n')

        thread = threading.Thread(target=peer)
        try:
            thread.start()
            self.assertEqual(ping(Path(os.ttyname(slave)), 2), 'POLIS_USB_OK uptime=3.25')
            thread.join(3)
            self.assertEqual(requests, [b'POLIS_PING\n'])
        finally:
            thread.join(3)
            os.close(master)
            os.close(slave)

    def test_enumeration_without_response_is_not_success(self):
        master, slave = pty.openpty()
        try:
            with self.assertRaisesRegex(UserError, 'did not answer'):
                ping(Path(os.ttyname(slave)), 0.05)
        finally:
            os.close(master)
            os.close(slave)

    def test_ping_retries_when_service_starts_after_usb_enumeration(self):
        master, slave = pty.openpty()
        requests = []

        def peer():
            received = b''
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if not select.select([master], [], [], 0.05)[0]:
                    continue
                received += os.read(master, 4096)
                while b'\n' in received:
                    line, received = received.split(b'\n', 1)
                    requests.append(line)
                    # The gadget drops traffic before userspace opens ttyGS0.
                    if len(requests) == 2:
                        os.write(master, b'POLIS_USB_OK uptime=4.25\n')
                        return

        thread = threading.Thread(target=peer)
        try:
            thread.start()
            self.assertEqual(ping(Path(os.ttyname(slave)), 1), 'POLIS_USB_OK uptime=4.25')
            thread.join(3)
            self.assertEqual(requests, [b'POLIS_PING', b'POLIS_PING'])
        finally:
            thread.join(3)
            os.close(master)
            os.close(slave)

    def test_selection_excludes_pico_and_rejects_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = root / 'tty'
            entries.mkdir()

            def add(index, product):
                device = root / f'usb-{index}'
                (device / 'interface').mkdir(parents=True)
                (device / 'product').write_text(product)
                entry = entries / f'ttyACM{index}'
                entry.mkdir()
                (entry / 'device').symlink_to(device / 'interface')

            add(0, 'Polis Wired Control')
            with self.assertRaisesRegex(UserError, 'found 0'):
                discover(entries)
            add(4, 'Polis_Pi_Diagnostics')
            self.assertEqual(discover(entries), Path('/dev/ttyACM4'))
            self.assertEqual(discover(entries, usb_path='usb-4'), Path('/dev/ttyACM4'))
            with self.assertRaisesRegex(UserError, 'found 0'):
                discover(entries, usb_path='wrong-port')
            add(7, 'Polis_Pi_Diagnostics')
            self.assertEqual(discover(entries, usb_path='usb-4'), Path('/dev/ttyACM4'))
            with self.assertRaisesRegex(UserError, 'found 2'):
                discover(entries)

    def test_target_service_answers_ping_and_does_not_execute_input(self):
        master, slave = pty.openpty()
        attrs = termios.tcgetattr(slave)
        attrs[4] = attrs[5] = termios.B9600
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        # Model g_serial's lack of set_termios: hardware speeds stay fixed,
        # and stty reports failure when its requested settings don't stick.
        # A normal PTY accepts baud changes, hiding this startup failure.
        stty = Path(fixture.name) / 'stty'
        stty.write_text(
            f'#!{sys.executable}\n'
            'import os, subprocess, sys, termios\n'
            'fd = os.open(sys.argv[2], os.O_RDWR | os.O_NOCTTY)\n'
            'before = termios.tcgetattr(fd)\n'
            f'result = subprocess.run([{shutil.which("stty")!r}, *sys.argv[1:]])\n'
            'after = termios.tcgetattr(fd)\n'
            'if after[4:6] != before[4:6]:\n'
            '    after[4:6] = before[4:6]\n'
            '    termios.tcsetattr(fd, termios.TCSANOW, after)\n'
            '    print("stty: cannot perform all requested operations", file=sys.stderr)\n'
            '    sys.exit(1)\n'
            'sys.exit(result.returncode)\n'
        )
        stty.chmod(0o755)
        script = (BOARD / 'rootfs-overlay/usr/libexec/polis-usb-diagnostic').read_text()
        script = script.replace('/dev/ttyGS0', os.ttyname(slave)).replace('sleep 1', 'sleep 0')
        process = subprocess.Popen(['sh', '-c', script], stderr=subprocess.PIPE,
                                   env={**os.environ, 'PATH': fixture.name + os.pathsep + os.environ['PATH']})

        def receive(expected):
            data = b''
            deadline = time.monotonic() + 3
            while expected not in data and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    data += os.read(master, 4096)
            self.assertIn(expected, data)

        try:
            receive(b'POLIS_USB_READY\n')
            self.assertEqual(termios.tcgetattr(slave)[5], termios.B9600)
            os.write(master, b'POLIS_PING\n')
            receive(b'POLIS_USB_OK uptime=')
            os.write(master, b'echo EXECUTED\n')
            receive(b'POLIS_USB_ERROR expected=POLIS_PING\n')
        finally:
            process.terminate()
            process.communicate(timeout=3)
            os.close(master)
            os.close(slave)

    def test_installer_preserves_storage_guard_and_adds_one_independent_service(self):
        spec = importlib.util.spec_from_file_location('install_smoke', BOARD / 'install-smoke.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'etc').mkdir()
            inittab = target / 'etc/inittab'
            inittab.write_text("::sysinit:/bin/sh -c '/etc/init.d/mount-data && /etc/init.d/rcS'\n")
            installer.install(target)
            self.assertNotIn('respawn:', inittab.read_text())  # stock baseline
            service = target / 'usr/libexec/polis-usb-diagnostic'
            service.write_text('#!/bin/sh\n')
            installer.install(target)
            installer.install(target)
            self.assertIn('\n::respawn:/usr/libexec/polis-usb-diagnostic\n', inittab.read_text())
            self.assertEqual(inittab.read_text().count('::respawn:/usr/libexec/polis-usb-diagnostic'), 1)
            self.assertIn('/etc/init.d/mount-data && /usr/libexec/polis-smoke-rcS', inittab.read_text())
            self.assertTrue(os.access(service, os.X_OK))

    def test_every_polis_profile_ships_diagnostics_without_card_edits(self):
        for profile in (REPO / 'configs').glob('polis_*_defconfig'):
            with self.subTest(profile=profile.name):
                config = read_config(profile)

                def source(symbol):
                    return Path(config[symbol].replace('$(BR2_EXTERNAL_POLIS_PATH)', str(REPO)))

                args = source('BR2_PACKAGE_RPI_FIRMWARE_CMDLINE_FILE').read_text().split()
                self.assertIn('console=ttyAMA0,115200', args)
                self.assertIn('polis.smoke=1', args)
                self.assertIn('g_serial.iProduct=Polis_Pi_Diagnostics', args)
                self.assertIn('dtoverlay=dwc2,dr_mode=peripheral', source('BR2_PACKAGE_RPI_FIRMWARE_CONFIG_FILE').read_text())
                self.assertIn('board/polis/rpi4/linux.fragment', config['BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES'])
