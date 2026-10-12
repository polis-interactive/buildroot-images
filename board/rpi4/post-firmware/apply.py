#!/usr/bin/env python3
"""Apply a prepared EEPROM step through the commissioned daemon, then clean SD."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import time

from prepare import ROOT, active, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--confirm-eeprom-write", required=True, choices=["bench-rpi4b"])
    args = parser.parse_args()
    directory = args.directory.resolve()
    with (directory / "apply.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        execute(directory, args)


def execute(directory, args):
    journal = directory / "apply.json"
    if journal.exists():
        raise SystemExit("Existing apply journal: inspect its recorded workflow; do not replay blindly")
    manifest = json.loads((directory / "manifest.json").read_text())
    cli = [str(ROOT / "package/polis-sbc-controller/.target/debug/polis-sbc-controller"),
           "--socket", str(ROOT / ".local/polis-sbc-controller-state/daemon.sock"),
           "--fixture", args.confirm_eeprom_write]

    def call(*command):
        return json.loads(subprocess.check_output(cli + list(command), text=True))

    for kind in ("update", "clean"):
        manifest[kind + "_image"] = str((directory / manifest[kind + "_image"]).resolve())
        if sha(manifest[kind + "_image"]) != manifest[kind + "_sha256"]:
            raise SystemExit(f"{kind} image hash mismatch")
    status = call("status")
    if status["workflow"]["active"] or not status["connection"]["connected"]:
        raise SystemExit("Fixture must be idle and connected")
    identity = (status["connection"]["boot_id"], status["connection"]["generation"])
    counters = status["connection"]["status"]["counters"]
    before = call("diagnostics")
    # The daemon verifies the diagnostic USB topology and capture hashes.
    def capture_path(value):
        if "path" not in value:
            raise RuntimeError(f"Unexpected diagnostics response: {value}")
        return Path(value["path"])

    def check_capture(path, expected):
        if manifest["version"] not in (path / "bootloader-version.txt").read_text():
            raise RuntimeError("EEPROM firmware version changed")
        if active((path / "bootloader-config.txt").read_text()) != active(expected):
            raise RuntimeError("EEPROM configuration does not match the prepared step")

    check_capture(capture_path(before), manifest["before_config"])
    records = {"before_capture": before, "runs": []}

    def save():
        journal.write_text(json.dumps(records, indent=2) + "\n")

    # The first boot performs the update/reset. Neither workflow is a benchmark.
    for kind in ("update", "clean"):
        operation = call("run", "--image", manifest[kind + "_image"], "--sha256",
                         manifest[kind + "_sha256"], "--confirm-fixture",
                         args.confirm_eeprom_write, "--auto-recover")
        record = dict(kind=kind, **operation)
        records["runs"].append(record)
        save()
        print(f"{kind}: started {operation['run_id']}", flush=True)
        deadline, previous = time.monotonic() + 1900, None
        while True:
            status = call("status")
            flow = status["workflow"]
            if flow["run_id"] != operation["run_id"]:
                raise RuntimeError("Workflow identity changed; inspect fixture before proceeding")
            if flow["stage"] != previous:
                previous = flow["stage"]
                print(f"{kind}: {previous}", flush=True)
            if not flow["active"]:
                record["final_status"] = status
                save()
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Observation deadline; workflow may still be running, do not replay")
            time.sleep(5)
        if flow["stage"] != "ready" or flow["recovery_attempts"] or flow["failures"]:
            raise RuntimeError("Workflow failed or used its single fallback; stop for review")
        connection = status["connection"]
        if (connection["boot_id"], connection["generation"]) != identity:
            raise RuntimeError("Pico identity/generation changed; stop")
        if connection["status"]["counters"] != counters:
            raise RuntimeError("Pico counters changed; inspect timing/transport before continuing")
        capture = json.loads((Path(operation["run_dir"]) / "capture.json").read_text())
        check_capture(capture_path(capture), manifest["after_config"])
        record["configuration_verified"] = True
        record["capture"] = capture
        save()
        print(f"{kind}: EEPROM version and configuration verified", flush=True)
    records["complete"] = True
    save()
    print("EEPROM step complete; clean normal SD image restored. Ready for cold-boot benchmark.")


if __name__ == "__main__":
    main()
