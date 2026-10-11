"""Check refusal paths without access to the fixture."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import apply
import prepare


class Guards(unittest.TestCase):
    def test_unfamiliar_config_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / "capture"
            capture.mkdir()
            (capture / "bootloader-version.txt").write_text(prepare.VERSION)
            config = "[all]\nBOOT_UART=0\nBOOT_ORDER=0xf1\n"
            (capture / "bootloader-config.txt").write_text(config)
            args = ["prepare.py", "--set", "NET_INSTALL_ENABLED=0", "--capture", str(capture), "--base-images",
                    str(root), "--output", str(root / "out")]
            with patch("sys.argv", args), patch.object(prepare.subprocess, "run") as run:
                with self.assertRaisesRegex(SystemExit, "Unexpected EEPROM configuration"):
                    prepare.main()
                run.assert_not_called()
            self.assertFalse((root / "out").exists())
            self.assertEqual((capture / "bootloader-config.txt").read_text(), config)

    def test_tampered_update_never_reaches_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "image"
            image.write_bytes(b"modified after review")
            (root / "manifest.json").write_text(json.dumps({
                "update_image": str(image), "update_sha256": "0" * 64}))
            args = ["apply.py", str(root), "--confirm-eeprom-write", "bench-rpi4b"]
            with patch("sys.argv", args), patch.object(apply.subprocess, "check_output") as call:
                with self.assertRaisesRegex(SystemExit, "update image hash mismatch"):
                    apply.main()
                call.assert_not_called()
            self.assertFalse((root / "apply.json").exists())

    def test_existing_journal_prevents_blind_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "apply.json").write_text('{"runs": [{"run_id": "existing"}]}')
            args = ["apply.py", str(root), "--confirm-eeprom-write", "bench-rpi4b"]
            with patch("sys.argv", args), patch.object(apply.subprocess, "check_output") as call:
                with self.assertRaisesRegex(SystemExit, "Existing apply journal"):
                    apply.main()
                call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
