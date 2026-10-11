"""Patch installation checks use one tiny source fixture, never a kernel build."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

BOARD = Path(__file__).resolve().parents[1] / 'board/rpi4'


class KernelEntryTest(unittest.TestCase):
    def test_apply_is_idempotent_and_rejects_partial_instrumentation(self):
        spec = importlib.util.spec_from_file_location('kernel_entry', BOARD/'install-kernel-entry.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        patch = (BOARD/'kernel-entry.patch').read_text().split('@@ -82,6 +82,48 @@\n')[1]
        original = '\n'.join(line[1:] for line in patch.splitlines() if line.startswith((' ', '-')))+'\n'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            head = root/'arch/arm64/kernel/head.S'
            head.parent.mkdir(parents=True)
            head.write_text(original)
            module.install(root)
            first = head.read_text()
            self.assertEqual(first.count('PLIS_ENTRY_V1'), 1)
            module.install(root)
            self.assertEqual(first,head.read_text())
            head.write_text(first.replace('mov\tx9, #0xfe200000','mov\tx9, #0xff200000'))
            with self.assertRaises(subprocess.CalledProcessError):
                module.install(root)
