"""Verify early probe placement without booting or touching GPIO hardware."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "board/polis/rpi4/install-gpio-test.py"
SMOKE_INSTALLER = REPO / "board/polis/rpi4/install-smoke.py"


class GpioProbeTest(unittest.TestCase):
    def test_starts_after_mounts_before_storage_and_stops_before_unmount(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            (target / "usr/bin").mkdir(parents=True)
            (target / "usr/bin/polis-gpio-test").touch()
            inittab = target / "etc/inittab"
            inittab.write_text((REPO / "board/polis/rpi4/filesystems/rootfs-overlay/etc/inittab").read_text())
            for _ in range(2):
                for installer in (SMOKE_INSTALLER, INSTALLER):
                    subprocess.run([sys.executable, str(installer), str(target)], check=True)
            text = inittab.read_text()
            self.assertEqual(text.count("polis-gpio-test start"), 1)
            self.assertEqual(text.count("polis-gpio-test stop"), 1)
            self.assertLess(text.index("/bin/mount -a"), text.index("polis-gpio-test start"))
            self.assertLess(text.index("polis-gpio-test start"), text.index("/etc/init.d/mount-data &&"))
            self.assertIn("/etc/init.d/mount-data && /usr/libexec/polis-smoke-rcS", text)
            self.assertLess(text.index("polis-gpio-test stop"), text.index("/etc/init.d/rcK"))
            self.assertLess(text.index("polis-gpio-test stop"), text.index("/bin/umount -a"))

    def test_disabled_package_leaves_init_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            inittab = target / "etc/inittab"
            inittab.write_text("stock init\n")
            subprocess.run([sys.executable, str(INSTALLER), str(target)], check=True)
            self.assertEqual(inittab.read_text(), "stock init\n")

    def test_unknown_mount_order_fails_without_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            (target / "usr/bin").mkdir(parents=True)
            (target / "usr/bin/polis-gpio-test").touch()
            inittab = target / "etc/inittab"
            inittab.write_text("::sysinit:/etc/init.d/rcS\n")
            result = subprocess.run([sys.executable, str(INSTALLER), str(target)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(inittab.read_text(), "::sysinit:/etc/init.d/rcS\n")
