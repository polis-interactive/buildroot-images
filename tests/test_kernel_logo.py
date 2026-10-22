"""Host logo preparation without an image or kernel build."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.common import UserError
from src.profiles import logo

REPO = Path(__file__).resolve().parents[1]


class KernelLogoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / 'logo.png'
        self.output = self.source.with_suffix('.ppm')
        for suffix in ('.png', '.ppm', '.json'):
            shutil.copyfile((REPO / logo.SOURCE).with_suffix(suffix), self.source.with_suffix(suffix))

    def test_current_logo_needs_no_converter_and_preserves_timestamps(self):
        before = self.output.stat().st_mtime_ns
        with patch.object(logo.shutil, 'which') as which, patch.object(logo.subprocess, 'run') as run:
            self.assertFalse(logo.prepare(self.source))
            self.assertFalse(logo.prepare(self.source, check=True))
            which.assert_not_called()
            run.assert_not_called()
        self.assertEqual(self.output.stat().st_mtime_ns, before)

    def test_source_change_check_and_missing_converter_leave_previous_logo(self):
        before = self.output.read_bytes()
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        with patch.object(logo.shutil, 'which', return_value=None) as which:
            with self.assertRaisesRegex(UserError, 'missing or stale'):
                logo.prepare(self.source, check=True)
            which.assert_not_called()
            with self.assertRaisesRegex(UserError, 'install ImageMagick on the build host'):
                logo.prepare(self.source)
        self.assertEqual(self.output.read_bytes(), before)

    def test_damaged_output_is_regenerated_then_reused(self):
        self.output.write_text('damaged')
        def convert(command, **kwargs):
            self.assertEqual(command[2:-1], logo.OPTIONS)
            shutil.copyfile((REPO / logo.SOURCE).with_suffix('.ppm'), command[-1])
        with patch.object(logo.shutil, 'which', return_value='/usr/bin/magick'), \
                patch.object(logo.subprocess, 'run', side_effect=convert) as run:
            self.assertTrue(logo.prepare(self.source))
            self.assertFalse(logo.prepare(self.source))
            run.assert_called_once()
        logo.validate_ppm(self.output)

    def test_failed_or_invalid_conversion_preserves_previous_logo_and_record(self):
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        paths = [self.output, self.output.with_suffix('.json')]
        before = [path.read_bytes() for path in paths]
        def invalid(command, **kwargs):
            Path(command[-1]).write_text('P6\n512 512\n255\n')
        for outcome, error in ((subprocess.CalledProcessError(1, 'magick'), subprocess.CalledProcessError),
                               (invalid, UserError)):
            with self.subTest(error=error), \
                    patch.object(logo.shutil, 'which', return_value='/usr/bin/magick'), \
                    patch.object(logo.subprocess, 'run', side_effect=outcome):
                with self.assertRaises(error):
                    logo.prepare(self.source)
                self.assertEqual([path.read_bytes() for path in paths], before)

    def test_wrong_size_png_fails_before_converter(self):
        self.source.write_bytes(b'\x89PNG\r\n\x1a\n' + bytes(16))
        with patch.object(logo.subprocess, 'run') as run:
            with self.assertRaisesRegex(UserError, '512x512 PNG'):
                logo.prepare(self.source)
            run.assert_not_called()
