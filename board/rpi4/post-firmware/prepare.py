#!/usr/bin/env python3
"""Prepare a separate Pi4 EEPROM self-update image; never access hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
VERSION = "57db150d63864d47e6c9071f6b086a5401eb4e92"
SOURCE_SHA = "3bf21d52b020a7e76a5fe9903e714a7fb5272c0a3a7bc1c1410f9cf45ed8b933"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def active(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def configuration(capture, settings, restore_config=None):
    version = (capture / "bootloader-version.txt").read_text()
    if VERSION not in version:
        raise SystemExit("Installed firmware differs from the pinned release; review before preparing")
    before = (capture / "bootloader-config.txt").read_text().rstrip() + "\n"
    # This step is deliberately scoped to the commissioned bench configuration.
    required = ["[all]", "BOOT_UART=0", "WAKE_ON_GPIO=1", "POWER_OFF_ON_HALT=0"]
    if active(before) not in (required, required + ["NET_INSTALL_ENABLED=0"]):
        raise SystemExit("Unexpected EEPROM configuration; preserve and review it first")
    if settings is not None and settings != ["NET_INSTALL_ENABLED=0"]:
        raise SystemExit("Unsupported EEPROM changes; this board backend supports NET_INSTALL_ENABLED=0")
    after = restore_config.read_text() if restore_config else (
        "\n".join(required + settings) + "\n")
    if active(after) not in (required, required + ["NET_INSTALL_ENABLED=0"]):
        raise SystemExit("This step only supports NET_INSTALL_ENABLED=0 or exact original rollback")
    return before, after, version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True,
                        help="Verified current-boot capture directory")
    parser.add_argument("--base-images", type=Path, required=True,
                        help="Preserved normal snapshot images directory")
    parser.add_argument("--output", type=Path, required=True)
    changes = parser.add_mutually_exclusive_group(required=True)
    changes.add_argument("--restore-config", type=Path, help="Restore a previously saved before.conf")
    changes.add_argument("--set", dest="settings", action="append", metavar="KEY=VALUE",
                         help="EEPROM setting declared by the profile")
    parser.add_argument("--mcopy", type=Path, help="mcopy executable (default: search PATH)")
    args = parser.parse_args()
    capture, base, out = [p.resolve() for p in (args.capture, args.base_images, args.output)]
    before, after, version = configuration(capture, args.settings, args.restore_config)
    if active(before) == active(after):
        raise SystemExit("Requested EEPROM configuration is already active")
    mcopy = str(args.mcopy) if args.mcopy else shutil.which("mcopy")
    if not mcopy:
        raise SystemExit("mcopy is required; install mtools or supply --mcopy from a completed profile build")
    tools = ROOT / "local/usbboot/rpi-eeprom"
    source = tools / "firmware-2711/old/stable/pieeprom-2025-11-05.bin"
    if sha(source) != SOURCE_SHA:
        raise SystemExit("Pinned firmware source hash mismatch")
    raw = (base / "sdcard.img").read_bytes()
    if raw[510:512] != b"\x55\xaa" or raw[450] not in (0x0b, 0x0c, 0x06):
        raise SystemExit("Expected an MBR image with FAT as its first partition")
    offset, sectors = struct.unpack_from("<II", raw, 454)
    offset, length = offset * 512, sectors * 512
    if raw[offset:offset + length] != (base / "boot.vfat").read_bytes():
        raise SystemExit("Normal snapshot FAT does not match its SD image")
    timestamp = max(int(time.time()), int(re.search(r"update-time (\d+)", version)[1]) + 1)
    out.mkdir(parents=True, exist_ok=False)
    (out / "before.conf").write_text(before)
    (out / "after.conf").write_text(after)
    (out / "before-version.txt").write_text(version)
    command = ["python3", str(tools / "rpi-eeprom-config")]
    # Both files use the same update timestamp; their executable sections match.
    for conf, name in [("before.conf", "rollback.bin"), ("after.conf", "pieeprom.upd")]:
        subprocess.run(command + ["--config", str(out / conf), "--timestamp", str(timestamp),
                                  "--out", str(out / name), str(source)], check=True)
        extracted = subprocess.check_output(command + [str(out / name)]).decode()
        if active(extracted) != active((out / conf).read_text()):
            raise SystemExit("EEPROM configuration round-trip failed")
    image_class = runpy.run_path(str(tools / "rpi-eeprom-config"))["BootloaderImage"]
    original, candidate = image_class(str(source)), image_class(str(out / "pieeprom.upd"))
    changed_files = []
    for section in original._sections:
        if section.filename:
            if original.get_file(section.filename) != candidate.get_file(section.filename):
                changed_files.append(section.filename)
        else:
            # Executable section bytes must remain byte-for-byte identical.
            start, end = section.offset, section.offset + section.length + 8
            if section.magic != 0x55aafeef and bytes(original._bytes[start:end]) != bytes(candidate._bytes[start:end]):
                raise SystemExit("Non-config firmware section changed")
    if set(changed_files) - {"bootconf.txt", "updatetime"}:
        raise SystemExit(f"Unexpected EEPROM changes: {changed_files}")
    subprocess.run([str(tools / "rpi-eeprom-digest"), "-c", "2711", "-i",
                    str(out / "pieeprom.upd"), "-o", str(out / "pieeprom.sig")],
                   env={**os.environ, "SOURCE_DATE_EPOCH": str(timestamp)}, check=True)
    fat = out / "update.vfat"
    shutil.copy2(base / "boot.vfat", fat)
    fat.chmod(fat.stat().st_mode | 0o200)
    for name in ("pieeprom.upd", "pieeprom.sig"):
        subprocess.run([str(mcopy), "-i", str(fat), str(out / name), "::" + name], check=True)
    extracted = out / "fat-check"
    extracted.mkdir()
    subprocess.run([str(mcopy), "-s", "-i", str(fat), "::*", str(extracted)], check=True)
    # Do not permit recovery/OTP provisioning files or firmware config changes.
    if any((extracted / n).exists() for n in ("recovery.bin", "recover4.elf", "recovery.elf")):
        raise SystemExit("Unexpected recovery executable on update FAT")
    original_files = out / "original-fat-check"
    original_files.mkdir()
    subprocess.run([str(mcopy), "-s", "-i", str(base / "boot.vfat"), "::*", str(original_files)], check=True)
    manifest = lambda directory: {str(p.relative_to(directory)): sha(p)
                                  for p in directory.rglob("*") if p.is_file()}
    old_files, new_files = manifest(original_files), manifest(extracted)
    changed = {k for k in old_files.keys() | new_files.keys() if old_files.get(k) != new_files.get(k)}
    if changed != {"pieeprom.upd", "pieeprom.sig"}:
        raise SystemExit(f"Unexpected FAT changes: {changed}")
    update = out / "sdcard-update.img"
    shutil.copy2(base / "sdcard.img", update)
    update.chmod(update.stat().st_mode | 0o200)
    with update.open("r+b") as stream:
        stream.seek(offset)
        stream.write(fat.read_bytes())
    actual = update.read_bytes()
    if actual[:offset] != raw[:offset] or actual[offset + length:] != raw[offset + length:]:
        raise SystemExit("Update image changed outside the boot FAT")
    result = dict(fixture="bench-rpi4b", version=VERSION, timestamp=timestamp,
                  before_config=before, after_config=after, source_sha256=SOURCE_SHA,
                  eeprom_sha256=sha(out / "pieeprom.upd"), rollback_sha256=sha(out / "rollback.bin"),
                  changed_eeprom_files=changed_files, update_image=update.name, update_sha256=sha(update),
                  clean_image=os.path.relpath(base / "sdcard.img", out), clean_sha256=sha(base / "sdcard.img"),
                  rollback_note="Reconstructed same-release firmware plus captured config; not an EEPROM dump")
    (out / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
