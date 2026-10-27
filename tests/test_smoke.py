"""Exercise serial protocol and process cleanup without needing QEMU or privileges."""
import contextlib
import csv
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src import cli
from src.diagnostics import smoke
from src.artifacts import snapshots
from src.common import Context, UserError


class SmokeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def watch(self, script, timeout=2):
        return smoke.watch([sys.executable, "-u", "-c", script], self.root / "serial.log",
                           "POLIS_SMOKE_READY services", timeout)

    def test_final_image_cannot_enter_smoke_timing(self):
        images = self.root / 'images'
        images.mkdir()
        (images / 'provisioning.json').write_text(json.dumps(dict(scope='first-boot', enabled=True)))
        with self.assertRaisesRegex(UserError, 'first-boot EEPROM setup is enabled'):
            smoke.boot_inputs(self.root)

    def test_split_marker_and_guest_uptime(self):
        result = self.watch("import os, time; os.write(1, b'POLIS_SMOKE_RE'); "
                            "time.sleep(.02); os.write(1, b'ADY services uptime=2.50\\r\\n'); "
                            "time.sleep(30)")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["guest_uptime_seconds"], 2.5)
        self.assertLess(result["seconds"], 2)
        self.assertIn(b"uptime=2.50", (self.root / "serial.log").read_bytes())

    def test_login_prompt_split_across_reads_without_newline(self):
        script = ("import os, time; os.write(1, b'Starting network: timeout!\\nboard log'); "
                  "time.sleep(.02); os.write(1, b'in: '); time.sleep(30)")
        result = smoke.watch([sys.executable, "-u", "-c", script], self.root / "serial.log",
                             "board login:", 2)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["matched_line"], "board login:")
        self.assertIsNone(result["guest_uptime_seconds"])
        self.assertLess(result["seconds"], 2)

    def test_partial_failure_wins_over_login_prompt(self):
        script = "import os, time; os.write(1, b'Kernel panic\\nboard login: '); time.sleep(30)"
        result = smoke.watch([sys.executable, "-u", "-c", script], self.root / "serial.log",
                             "board login:", 2)
        self.assertEqual(result["status"], "failed")

    def test_service_marker_waits_for_complete_uptime(self):
        result = self.watch("import os, time; os.write(1, b'POLIS_SMOKE_READY services uptime=2.'); "
                            "time.sleep(.02); os.write(1, b'50\\n'); time.sleep(30)")
        self.assertEqual(result["guest_uptime_seconds"], 2.5)

    def test_silent_and_partial_lines_timeout_and_reap_process(self):
        real_popen = subprocess.Popen
        for prefix in ("", "print('partial', end='', flush=True); "):
            with self.subTest(prefix=prefix):
                processes = []

                def spawn(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)
                    return process

                with patch.object(smoke.subprocess, "Popen", side_effect=spawn):
                    result = self.watch(f"import time; {prefix}time.sleep(30)", timeout=.15)
                self.assertEqual(result["status"], "timeout")
                self.assertLess(result["seconds"], 1)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_closed_output_does_not_wait_forever_for_process_exit(self):
        result = self.watch("import os, time; os.close(1); os.close(2); time.sleep(30)")
        self.assertEqual(result["status"], "exited")
        self.assertLess(result["seconds"], 2)

    def test_clean_exit_without_marker_is_failure(self):
        result = self.watch("print('not ready')")
        self.assertEqual(result["status"], "exited")
        self.assertEqual(result["returncode"], 0)

    def test_failures_win_over_marker(self):
        for failure in ("POLIS_SMOKE_FAIL storage", "Kernel panic - not syncing"):
            with self.subTest(failure=failure):
                result = self.watch(f"import os; os.write(1, {failure!r}.encode() + "
                                    "b'\\nPOLIS_SMOKE_READY services\\n')")
                self.assertEqual(result["status"], "failed")

    def source(self):
        source = self.root / "snapshot with spaces"
        images = source / "images"
        (images / "rpi-firmware").mkdir(parents=True)
        for name in ("sdcard.img", "Image", "bcm2711-rpi-4-b.dtb"):
            (images / name).write_bytes(b"unchanged")
        (images / "rpi-firmware/cmdline.txt").write_text(
            "root=/dev/mmcblk0p2 rootwait rootfstype=ext4 ro console=null\n")
        (source / "configs").mkdir()
        (source / "configs/buildroot.config").write_text("BR2_TARGET_ROOTFS_EXT2=y\n")
        (source / "metadata.txt").write_text("name=snapshot with spaces\n")
        (source / "SHA256SUMS").touch()
        return source

    def test_initramfs_adds_archive_and_preserves_boot_arguments(self):
        source = self.source()
        (source / "configs/buildroot.config").write_text(
            "BR2_TARGET_ROOTFS_CPIO=y\nBR2_TARGET_ROOTFS_CPIO_LZ4=y\n")
        (source / "images/rpi-firmware/cmdline.txt").write_text("rdinit=/init ro console=null\n")
        with self.assertRaisesRegex(UserError, "rootfs.cpio.lz4"):
            smoke.boot_inputs(source)
        (source / "images/rootfs.cpio.lz4").write_bytes(b"archive")
        files, cmdline = smoke.boot_inputs(source)
        self.assertEqual(files[-1].name, "rootfs.cpio.lz4")
        self.assertEqual(cmdline, "rdinit=/init ro console=null polis.smoke=1")

    def test_checks_are_opt_in_and_preserve_boot_arguments(self):
        source = self.source()
        cmdline = source / "images/rpi-firmware/cmdline.txt"
        cmdline.write_text("root=/dev/mmcblk0p2 rootwait console=null polis.smoke=0 polis.smoke.checks=1\n")
        base = "root=/dev/mmcblk0p2 rootwait console=null polis.smoke=1"
        self.assertEqual(smoke.boot_inputs(source)[1], base)
        for target in ("storage", "services"):
            self.assertEqual(smoke.boot_inputs(source, f"POLIS_SMOKE_READY {target}")[1],
                             base + " polis.smoke.checks=1")
        self.assertEqual(smoke.boot_inputs(source, "buildroot login:")[1], base)

    def test_incomplete_snapshot_rejected(self):
        source = self.source()
        (source / ".incomplete").touch()
        with self.assertRaisesRegex(UserError, "incomplete"):
            smoke.boot_inputs(source)

    def test_fresh_disks_report_and_source_unchanged(self):
        source = self.source()
        ctx = Context(self.root, self.root, self.root, self.root)
        results = self.root / "results with spaces"
        original = (source / "images/sdcard.img").read_bytes()

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "QEMU test raspi4b\n", "")

        def boot(command, log, expect, timeout):
            self.assertEqual(expect, "POLIS_SMOKE_READY init")
            self.assertNotIn("polis.smoke.checks=1", command[-1])
            drive = command[command.index("-drive") + 1]
            disk = Path(drive.split("file=", 1)[1].split(",if=", 1)[0])
            self.assertEqual(disk.read_bytes(), original + b"\0" * (16 - len(original)))
            disk.write_bytes(b"guest modifications")
            log.write_text("POLIS_SMOKE_READY init uptime=1.00\n")
            return dict(status="passed", seconds=2.0, guest_uptime_seconds=1.0)

        with patch.object(smoke, "executable", return_value="fake"), \
                patch.object(smoke, "run", side_effect=fake_run), \
                patch.object(smoke, "watch", side_effect=boot), \
                contextlib.redirect_stdout(StringIO()):
            smoke.smoke(ctx, source, runs=2, results=results)
            with self.assertRaises(FileExistsError):
                smoke.smoke(ctx, source, results=results)
        report = json.loads((results / "results.json").read_text())
        self.assertEqual(report["summary"]["passed"], 2)
        self.assertEqual(report["summary"]["median_seconds"], 2.0)
        self.assertEqual((source / "images/sdcard.img").read_bytes(), original)
        self.assertEqual(list(results.glob("disk-*")), [])
        with (self.root / "benchmarks/software-boot-times.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["passed"], "2/2")
        self.assertEqual(rows[0]["median_s"], "2.000")
        self.assertEqual(rows[0]["source"], source.name)
        self.assertEqual(rows[0]["target"], "init")

    def test_benchmark_excludes_failed_times_and_keeps_distinct_sessions(self):
        report = dict(source=str(self.root / ".output/board"), expect="POLIS_SMOKE_READY services",
                      runs=[dict(status="timeout", seconds=1.0)])
        results = self.root / "failed"
        path = smoke.record_benchmark(self.root, results, report)
        smoke.record_benchmark(self.root, results, report)  # Re-importing is idempotent.
        report["runs"].extend([dict(status="passed", seconds=10.0), dict(status="passed", seconds=20.0)])
        smoke.record_benchmark(self.root, self.root / "mixed", report)
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["passed"], "0/1")
        self.assertEqual(rows[0]["median_s"], "")
        self.assertEqual(rows[1]["passed"], "2/3")
        self.assertEqual(rows[1]["median_s"], "15.000")
        self.assertEqual(rows[1]["min_s"], "10.000")
        self.assertEqual(rows[1]["max_s"], "20.000")

    def test_invalid_limits_rejected_before_source_access(self):
        ctx = Context(self.root, self.root, self.root, self.root)
        for values in ({"runs": 0}, {"timeout": -1}, {"timeout": float("nan")},
                       {"timeout": float("inf")}, {"expect": ""}):
            with self.subTest(values=values), self.assertRaises(UserError):
                smoke.smoke(ctx, self.root / "missing", **values)

    def test_cli_requires_explicit_selection_noninteractively(self):
        source = self.source()
        args = ["smoke", "--snapshots", str(self.root)]
        with patch.object(cli, "REPO", self.root), patch("sys.stdin.isatty", return_value=False), \
                patch.object(smoke, "smoke") as boot, contextlib.redirect_stdout(StringIO()) as output:
            self.assertEqual(cli.main(args), 0)
            boot.assert_not_called()
            self.assertIn(source.name, output.getvalue())
            self.assertIn("--snapshot", output.getvalue())
            self.assertEqual(cli.main([*args, "--snapshot", source.name, "--runs", "3"]), 0)
            boot.assert_not_called()
            self.assertIn("--target", output.getvalue())
            self.assertEqual(cli.main([*args, "--snapshot", source.name, "--target", "services"]), 0)
            self.assertEqual(boot.call_args.args[1], source)
            self.assertEqual(boot.call_args.kwargs["runs"], 3)
            self.assertEqual(boot.call_args.kwargs["expect"], "POLIS_SMOKE_READY services")

    def test_default_interactive_menu_selects_snapshots(self):
        source = self.source()
        collection = self.root / ".snapshots"
        collection.mkdir()
        source.rename(collection / "ready")
        with patch.object(cli, "REPO", self.root), patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", side_effect=["1", "3", ""]) as prompts, \
                patch.object(smoke, "smoke") as boot, \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(cli.main(["smoke"]), 0)
            self.assertEqual(boot.call_args.args[1], collection / "ready")
            self.assertEqual(boot.call_args.kwargs["expect"], "POLIS_SMOKE_READY services")
            self.assertEqual(boot.call_args.kwargs["runs"], 3)
            self.assertEqual(prompts.call_count, 3)

    def test_interactive_run_count_and_invalid_input(self):
        source = self.source()
        args = ["smoke", "--snapshots", str(self.root), "--snapshot", source.name,
                "--target", "storage"]
        for answer, expected_runs in (("5", 5), ("  ", 3), ("0", None), ("-2", None),
                                      ("1.5", None), ("no", None)):
            with self.subTest(answer=answer), patch("sys.stdin.isatty", return_value=True), \
                    patch("builtins.input", return_value=answer), patch.object(smoke, "smoke") as boot, \
                    contextlib.redirect_stderr(StringIO()):
                self.assertEqual(cli.main(args), 0 if expected_runs else 1)
                if expected_runs:
                    self.assertEqual(boot.call_args.kwargs["runs"], expected_runs)
                    self.assertEqual(boot.call_args.kwargs["expect"], "POLIS_SMOKE_READY storage")
                else:
                    boot.assert_not_called()

    def test_explicit_smoke_options_skip_prompts(self):
        source = self.source()
        for options, expect in ((["--target", "init"], "POLIS_SMOKE_READY init"),
                                (["--expect", "custom login:"], "custom login:")):
            with self.subTest(options=options), patch("sys.stdin.isatty", return_value=True), \
                    patch("builtins.input") as prompt, patch.object(smoke, "smoke") as boot:
                self.assertEqual(cli.main(["smoke", "--snapshots", str(self.root), "--snapshot",
                                           source.name, "--runs", "2", *options]), 0)
                prompt.assert_not_called()
                self.assertEqual(boot.call_args.kwargs["runs"], 2)
                self.assertEqual(boot.call_args.kwargs["expect"], expect)

    def test_target_cancellation_does_not_boot(self):
        source = self.source()
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""), \
                patch.object(smoke, "smoke") as boot, contextlib.redirect_stdout(StringIO()):
            self.assertEqual(cli.main(["smoke", "--snapshots", str(self.root),
                                       "--snapshot", source.name]), 0)
            boot.assert_not_called()

    def test_target_and_custom_expect_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            cli.parser().parse_args(["smoke", "--target", "services", "--expect", "custom"])
        self.assertEqual(error.exception.code, 2)

    def test_snapshot_discovery_excludes_outputs_incomplete_and_empty_images(self):
        source = self.source()
        ctx = Context(self.root, self.root, self.root, self.root)
        (self.root / ".output/images").mkdir(parents=True)
        (self.root / ".output/images/sdcard.img").write_bytes(b"working build")
        self.assertEqual(snapshots.completed(ctx), [source.name])
        with self.assertRaises(UserError):
            snapshots.source_for(ctx, ".output")
        with self.assertRaises(UserError):
            snapshots.source_for(ctx, "../" + source.name)
        (source / ".incomplete").touch()
        self.assertEqual(snapshots.completed(ctx), [])
        with self.assertRaises(UserError):
            snapshots.source_for(ctx, source.name)
        (source / ".incomplete").unlink()
        (source / "images/sdcard.img").write_bytes(b"")
        self.assertEqual(snapshots.completed(ctx), [])

    def test_smoke_no_longer_accepts_build_output_options(self):
        for option in ("--board", "--output", "--source-output"):
            with self.subTest(option=option), contextlib.redirect_stderr(StringIO()), \
                    self.assertRaises(SystemExit) as error:
                cli.parser().parse_args(["smoke", option, ".output/foo"])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
