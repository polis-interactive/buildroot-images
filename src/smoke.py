"""Disposable, direct-kernel QEMU Pi 4 boots with serial readiness targets."""
from datetime import datetime, timezone
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import statistics
import subprocess
import tempfile
import time

from .common import UserError, read_config, run
from .snapshots import digest, source_for


def record_benchmark(repo: Path, results: Path, report: dict) -> Path:
    """Keep one compact comparison row per session; JSON remains the raw evidence."""
    def relative(path):
        return os.path.relpath(path, repo)

    passed = [r["seconds"] for r in report["runs"] if r["status"] == "passed"]
    row = {
        "source": relative(report["source"]),
        "target": report["expect"].removeprefix("POLIS_SMOKE_READY "),
        "passed": f"{len(passed)}/{len(report['runs'])}",
        "median_s": f"{statistics.median(passed):.3f}" if passed else "",
        "min_s": f"{min(passed):.3f}" if passed else "",
        "max_s": f"{max(passed):.3f}" if passed else "",
        "results": relative(results),
    }
    destination = repo / "benchmarks/software-boot-times.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a+", newline="") as stream:
        # Concurrent smoke sessions must not discard each other's measurements.
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        reader = csv.DictReader(stream)
        if reader.fieldnames is not None and reader.fieldnames != list(row):
            raise UserError(f"Unexpected columns in {destination}; existing benchmarks were preserved.")
        rows = list(reader)
        for index, existing in enumerate(rows):
            if existing["results"] == row["results"]:
                rows[index] = row
                break
        else:
            rows.append(row)
        rows.sort(key=lambda entry: (entry["target"], entry["source"]))
        stream.seek(0)
        stream.truncate()
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerows(rows)
    return destination


def executable(repo: Path, name: str, override: str | None = None) -> str:
    path = shutil.which(override or name)
    if path:
        return str(Path(path).resolve())
    local = repo / ".cache/qemu/root/usr/bin" / name
    if not override and os.access(local, os.X_OK):
        return str(local)
    raise UserError(f"Missing {override or name}. Install qemu-system-arm and "
                    "device-tree-compiler, or provide --qemu/--fdtput.")


def boot_inputs(source: Path, expect: str = "POLIS_SMOKE_READY init") -> tuple[list[Path], str]:
    if (source / ".incomplete").exists():
        raise UserError("Cannot boot an incomplete snapshot.")
    images = source / "images"
    files = [images / name for name in ("sdcard.img", "Image", "bcm2711-rpi-4-b.dtb")]
    cmdline = images / "rpi-firmware/cmdline.txt"
    files.append(cmdline)
    config = source / "configs/buildroot.config"
    if not config.is_file():
        raise UserError(f"No Buildroot configuration in {source}.")
    saved = read_config(config)
    if saved.get("BR2_TARGET_ROOTFS_CPIO") == "y":
        if saved.get("BR2_TARGET_ROOTFS_CPIO_LZ4") != "y":
            raise UserError("The initramfs smoke runner currently requires rootfs.cpio.lz4.")
        files.append(images / "rootfs.cpio.lz4")
    for path in files:
        if not path.is_file() or not path.stat().st_size:
            raise UserError(f"Missing boot artifact: {path}. Build and capture a new snapshot first.")
    # Retain the image's root/filesystem arguments; only enable the probe.
    args = [arg for arg in cmdline.read_text().split()
            if not arg.startswith(("polis.smoke=", "polis.smoke.checks="))]
    args.append("polis.smoke=1")
    if expect in ("POLIS_SMOKE_READY storage", "POLIS_SMOKE_READY services"):
        args.append("polis.smoke.checks=1")
    return files, " ".join(args)


def adapt_dtb(fdtput: str, source: Path, destination: Path) -> None:
    """QEMU supplies no Pi firmware overlays and attaches SD to the older host."""
    shutil.copyfile(source, destination)
    for node, prop, value in (
        ("/aliases", "mmc0", "/soc/mmcnr@7e300000"),
        ("/emmc2bus/mmc@7e340000", "status", "disabled"),
        ("/soc/serial@7e201000/bluetooth", "status", "disabled"),
        ("/aliases", "serial0", "/soc/serial@7e201000"),
    ):
        run([fdtput, "-t", "s", destination, node, prop, value], capture=True)
    for prop in ("mmc1", "serial1"):
        run([fdtput, "-d", destination, "/aliases", prop], capture=True)


def watch(command: list[str], log: Path, expect: str, timeout: float) -> dict:
    """Drain output without blocking on newlines; always reap the emulator."""
    started = time.monotonic()
    result = {"status": "timeout", "seconds": None, "guest_uptime_seconds": None}
    pending = b""
    process = None
    try:
        with log.open("wb") as stream, selectors.DefaultSelector() as selector:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() - started < timeout:
                events = selector.select(min(0.1, max(0, timeout - (time.monotonic() - started))))
                if not events:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    result["status"] = "exited"
                    result["returncode"] = process.poll()
                    break
                stream.write(chunk)
                stream.flush()
                pending += chunk
                lines = pending.split(b"\n")
                pending = lines.pop()[-65536:]
                # Failure wins over a readiness marker in the same read.
                complete = [line.decode(errors="replace").strip() for line in lines]
                partial = pending.decode(errors="replace").strip()
                if any(token in "\n".join([*complete, partial]) for token in
                       ("POLIS_SMOKE_FAIL", "Kernel panic", "Oops:")):
                    result["status"] = "failed"
                    break
                # Getty prompts have no newline. Keep probe lines intact so an
                # uptime split across reads is never recorded as a partial number.
                candidates = [*complete, partial] if expect.endswith("login:") else complete
                for line in candidates:
                    if expect in line:
                        result["status"] = "passed"
                        match = re.search(r"uptime=([0-9.]+)", line)
                        if match:
                            result["guest_uptime_seconds"] = float(match[1])
                        result["matched_line"] = line
                        break
                if result["status"] == "passed":
                    break
    except KeyboardInterrupt:
        result["status"] = "interrupted"
    finally:
        result["seconds"] = round(time.monotonic() - started, 6)
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()
    return result


def smoke(ctx, source: Path, *, runs=1, timeout=120.0, expect: str | None = None,
          results: Path | None = None, qemu=None, fdtput=None) -> Path:
    if runs < 1 or not math.isfinite(timeout) or timeout <= 0 or (expect is not None and not expect.strip()):
        raise UserError("Use --runs >= 1, a finite positive --timeout, and a nonempty --expect.")
    if expect is not None and ("\n" in expect or "\r" in expect):
        raise UserError("--expect must fit on one serial line.")
    source = source.resolve(strict=True)
    if source != source_for(ctx, source.name):
        raise UserError("Smoke tests require a completed snapshot from the selected --snapshots directory.")
    expect = "POLIS_SMOKE_READY init" if expect is None else expect
    files, cmdline = boot_inputs(source, expect)
    qemu = executable(ctx.repo, "qemu-system-aarch64", qemu)
    fdtput = executable(ctx.repo, "fdtput", fdtput)
    version = run([qemu, "--version"], capture=True).stdout.splitlines()[0]
    if "raspi4b" not in run([qemu, "-machine", "help"], capture=True).stdout:
        raise UserError("This QEMU lacks raspi4b; install QEMU 9 or newer with that machine.")
    results = (results or ctx.repo / ".cache/smoke" /
               datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")).resolve()
    if results.is_relative_to(source):
        raise UserError("Keep smoke results outside the source snapshot.")
    results.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1, "source": str(source), "qemu": version,
        "host": platform.platform(), "machine": "raspi4b", "accelerator": "tcg",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "expect": expect, "timeout_seconds": timeout, "cmdline": cmdline,
        "disk_state": "fresh disposable copy of source for each run",
        "artifacts": {str(p.relative_to(source)): {"sha256": digest(p), "bytes": p.stat().st_size}
                      for p in files},
        "runs": [],
    }
    dtb = results / "qemu.dtb"
    adapt_dtb(fdtput, files[2], dtb)
    report["adapted_dtb_sha256"] = digest(dtb)
    print(f"Serial logs: {results}", flush=True)
    print(f"Readiness target: {expect}", flush=True)
    print("Direct kernel boot; timings are local emulation measurements, not Pi boot times.", flush=True)
    for index in range(1, runs + 1):
        with tempfile.TemporaryDirectory(prefix="disk-", dir=results) as temporary:
            disk = Path(temporary) / "sdcard.img"
            # Copy bytes rather than links: snapshots may be read-only, and no boot may alter them.
            shutil.copyfile(files[0], disk)
            with disk.open("r+b") as stream:
                stream.truncate(1 << (disk.stat().st_size - 1).bit_length())
            command = [qemu, "-M", "raspi4b", "-accel", "tcg", "-smp", "4", "-m", "2G",
                       "-kernel", str(files[1]), "-dtb", str(dtb),
                       "-drive", f"file={str(disk).replace(',', ',,')},if=sd,format=raw",
                       "-display", "none", "-serial", "stdio", "-monitor", "none",
                       "-nic", "none", "-no-reboot", "-append", cmdline]
            if files[-1].name == "rootfs.cpio.lz4":
                command.extend(["-initrd", str(files[-1])])
            log = results / f"run-{index:03}.log"
            record = watch(command, log, expect, timeout)
            record.update(run=index, log=log.name, command=command)
            report["runs"].append(record)
        passed = [r["seconds"] for r in report["runs"] if r["status"] == "passed"]
        report["summary"] = {"passed": len(passed), "total": len(report["runs"]),
                             "median_seconds": statistics.median(passed) if passed else None,
                             "min_seconds": min(passed) if passed else None,
                             "max_seconds": max(passed) if passed else None}
        (results / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        benchmark = record_benchmark(ctx.repo, results, report)
        print(f"Run {index}/{runs}: {record['status']} in {record['seconds']:.3f}s ({log.name})",
              flush=True)
        if record["status"] == "interrupted":
            print(f"Benchmarks: {benchmark}", flush=True)
            raise KeyboardInterrupt
    if any(r["status"] != "passed" for r in report["runs"]):
        raise UserError(f"Smoke target not reached in every run. See {benchmark} and logs in {results}. "
                        f"Expected {expect!r}; check the serial log and the image's readiness configuration.")
    summary = report["summary"]
    print(f"Passed {summary['passed']}/{summary['total']}; median {summary['median_seconds']:.3f}s "
          f"(range {summary['min_seconds']:.3f}–{summary['max_seconds']:.3f}s)")
    print(f"Benchmarks: {benchmark}")
    return results
