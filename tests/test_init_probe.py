"""Verify the common probe's position without building an image."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "board/rpi4/install-smoke.py"


class InitProbeTest(unittest.TestCase):
    def test_probe_runs_after_rcs_before_getty_and_installs_idempotently(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            inittab = target / "etc/inittab"
            original = "::sysinit:/bin/hostname -F /etc/hostname\n::sysinit:/etc/init.d/rcS\nconsole::respawn:/sbin/getty console\n"
            inittab.write_text(original)
            for _ in range(2):
                subprocess.run([sys.executable, str(INSTALLER), str(target)], check=True)
            self.assertEqual(inittab.read_text(), original.replace(
                "/etc/init.d/rcS", "/usr/libexec/polis-smoke-rcS"))
            installed = target / "usr/libexec/polis-smoke-rcS"
            self.assertEqual(installed.stat().st_mode & 0o777, 0o755)

    def test_data_mount_guard_remains_before_wrapped_rcs(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            inittab = target / "etc/inittab"
            original = "::sysinit:/bin/sh -c '/etc/init.d/mount-data && /etc/init.d/rcS'\n"
            inittab.write_text(original)
            for _ in range(2):
                subprocess.run([sys.executable, str(INSTALLER), str(target)], check=True)
            self.assertEqual(inittab.read_text(), original.replace(
                "/etc/init.d/rcS", "/usr/libexec/polis-smoke-rcS"))

    def test_missing_rcs_rejected_without_editing_inittab(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "etc").mkdir()
            inittab = target / "etc/inittab"
            inittab.write_text("unsupported init\n")
            result = subprocess.run([sys.executable, str(INSTALLER), str(target)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(inittab.read_text(), "unsupported init\n")


if __name__ == "__main__":
    unittest.main()
