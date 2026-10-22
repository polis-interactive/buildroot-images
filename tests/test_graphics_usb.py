"""Exercise the real C supervisor against synthetic sysfs and PTY device nodes."""
import importlib.util
import os
from pathlib import Path
import pty
import signal
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / 'package/polis-graphics-usb'


class GraphicsPayloadTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('graphics_prepare', PACKAGE / 'prepare.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.binary = self.root / 'app'
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', elf, 18, 183)
        self.binary.write_bytes(elf)
        self.profile = self.root / 'profile.toml'
        self.profile.write_text('vid = 0x2e8a\npid = 0x0009\nmanufacturer = "Polis"\n'
                                'product = "Polis Screen Graphics"\ninterface = 0\n')

    def test_rejects_new_target_dependency(self):
        with patch.object(self.module.subprocess, 'check_output',
                          return_value='0x1 (NEEDED) Shared library: [libudev.so.1]'):
            with self.assertRaisesRegex(ValueError, 'Unapproved target dependencies'):
                self.module.validate(self.binary, self.profile)

    def test_rejects_descriptor_drift(self):
        self.profile.write_text(self.profile.read_text().replace('Polis Screen Graphics', 'Other'))
        with patch.object(self.module.subprocess, 'check_output', return_value=''):
            with self.assertRaisesRegex(ValueError, 'descriptor contract changed'):
                self.module.validate(self.binary, self.profile)


class GraphicsUSBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='graphics-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sys = self.root / 'sys'
        self.dev = self.root / 'dev'
        (self.sys / 'class/tty').mkdir(parents=True)
        self.dev.mkdir()
        self.process = None
        self.addCleanup(self.stop)
        self.app = self.root / 'app'
        self.app.write_text('#!/bin/sh\necho "$$ $*" >> "' + str(self.root / 'starts') + '"\n'
                            'echo "worker ready"\nexec sleep 300\n')
        self.app.chmod(0o755)
        self.mdev = self.root / 'mdev'
        # Simulate BusyBox's initial_scan contract: ACTION/MDEV, no DEVPATH.
        self.mdev.write_text('#!/bin/sh\nfor p in "' + str(self.sys) + '/class/tty/"*; do\n'
                             '[ -e "$p" ] || continue\n'
                             'env -u DEVPATH ACTION=add MDEV="${p##*/}" "' +
                             str(self.root / 'supervisor') + '" event\ndone\n')
        self.mdev.chmod(0o755)
        defines = dict(SYSROOT=self.sys, DEVROOT=self.dev, RUNDIR=self.root / 'run',
                       APP=self.app, MDEV=self.mdev)
        self.binary = self.root / 'supervisor'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror',
                        *[f'-D{k}="{v}"' for k, v in defines.items()],
                        str(PACKAGE / 'src/graphics-usb.c'), '-o', str(self.binary)], check=True)
        self.log = (self.root / 'log').open('w+')
        self.addCleanup(self.log.close)

    def attach(self, number=0, **attrs):
        name = f'ttyACM{number}'
        usb = self.sys / f'devices/usb1/1-{number + 1}'
        interface = usb / f'1-{number + 1}:1.0'
        tty = interface / 'tty' / name
        tty.mkdir(parents=True)
        defaults = dict(idVendor='2e8a', idProduct='0009', manufacturer='Polis',
                        product='Polis Screen Graphics', bInterfaceNumber='00')
        defaults.update(attrs)
        for key, value in defaults.items():
            ((interface if key == 'bInterfaceNumber' else usb) / key).write_text(value + '\n')
        (tty / 'device').symlink_to(interface)
        (self.sys / 'class/tty' / name).symlink_to(tty)
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        (self.dev / name).symlink_to(os.ttyname(slave))
        return '/' + str(tty.relative_to(self.sys))

    def detach(self, number=0):
        name = f'ttyACM{number}'
        link = self.sys / 'class/tty' / name
        path = '/' + str(link.resolve().relative_to(self.sys))
        link.unlink()
        (self.dev / name).unlink()
        # Keep old inode allocated to deterministically distinguish a new attachment.
        usb = self.sys / f'devices/usb1/1-{number + 1}'
        usb.rename(usb.with_name(usb.name + '-removed'))
        self.event('remove', number, path)

    def start(self):
        self.process = subprocess.Popen([str(self.binary)], stderr=self.log)
        self.until(lambda: (self.root / 'run/events').is_socket())

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)

    def event(self, action='add', number=0, path=None):
        env = dict(os.environ, ACTION=action, MDEV=f'ttyACM{number}')
        env.pop('DEVPATH', None)
        if path is not None:
            env['DEVPATH'] = path
        subprocess.run([str(self.binary), 'event'], env=env, check=True)

    def starts(self):
        p = self.root / 'starts'
        return p.read_text().splitlines() if p.exists() else []

    def until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.02)
        self.log.flush()
        self.fail('Condition timed out; supervisor log: ' + (self.root / 'log').read_text())

    def alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_coldplug_two_picos_duplicate_events_crash_and_restart(self):
        self.attach(0)
        self.attach(1)
        self.start()
        self.until(lambda: len(self.starts()) == 2)
        for number in (0, 1):
            self.event(number=number)
        duplicate = subprocess.run([str(self.binary)], stderr=subprocess.DEVNULL)
        self.assertNotEqual(duplicate.returncode, 0)
        time.sleep(.15)
        self.assertEqual(len(self.starts()), 2)
        self.assertEqual({line.split()[1] for line in self.starts()}, {'--port'})
        self.assertEqual({line.split()[2] for line in self.starts()},
                         {str(self.dev / 'ttyACM0'), str(self.dev / 'ttyACM1')})
        old = int(self.starts()[0].split()[0])
        os.kill(old, signal.SIGKILL)
        self.until(lambda: len(self.starts()) == 3)
        pids = [int(line.split()[0]) for line in self.starts()]
        self.stop()
        self.assertTrue(all(not self.alive(pid) for pid in pids))
        self.start()
        self.until(lambda: len(self.starts()) == 5)

    def test_hotplug_detach_rapid_replug_and_stale_remove(self):
        self.start()
        path = self.attach()
        self.event(path=path)
        self.until(lambda: len(self.starts()) == 1)
        old = int(self.starts()[0].split()[0])
        self.detach()
        self.attach()
        self.event(path=path)
        self.until(lambda: len(self.starts()) == 2)
        self.assertFalse(self.alive(old))
        current = int(self.starts()[1].split()[0])
        self.event('remove', path='/devices/an-old-different-port/ttyACM0')
        time.sleep(.1)
        self.assertTrue(self.alive(current))
        self.event('remove', path=path)
        self.until(lambda: not self.alive(current))
        time.sleep(1.1)
        self.assertEqual(len(self.starts()), 2)

    def test_excludes_unrelated_cdc_bootsel_and_data_interface(self):
        for number, attrs in enumerate((dict(product='Other CDC'), dict(manufacturer='Other'),
                                        dict(idProduct='0003'), dict(idVendor='1234'),
                                        dict(bInterfaceNumber='01'))):
            self.attach(number, **attrs)
        self.start()
        time.sleep(.2)
        self.assertEqual(self.starts(), [])
        path = self.attach(5)
        self.event(number=5, path=path)
        self.until(lambda: len(self.starts()) == 1)

    def test_detach_kills_unresponsive_worker(self):
        self.app.write_text('#!/bin/sh\ntrap "" TERM\necho "$$ --port $2" >> "' +
                            str(self.root / 'starts') + '"\nwhile :; do sleep 300; done\n')
        self.attach()
        self.start()
        self.until(lambda: len(self.starts()) == 1)
        pid = int(self.starts()[0].split()[0])
        self.detach()
        self.until(lambda: not self.alive(pid))

    def test_mdev_rule_preserves_standard_rules_and_is_idempotent(self):
        spec = importlib.util.spec_from_file_location('install_mdev', PACKAGE / 'install-mdev.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        (self.root / 'etc').mkdir()
        conf = self.root / 'etc/mdev.conf'
        original = 'null root:root 666\n$MODALIAS=.* root:root 660 @modprobe "$MODALIAS"\n'
        conf.write_text(original)
        module.install(self.root)
        module.install(self.root)
        self.assertEqual(conf.read_text(), module.RULE + '\n' + original)


if __name__ == '__main__':
    unittest.main()
