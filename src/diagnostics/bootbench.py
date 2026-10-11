"""Archive hardware captures and compare firmware milestones, without clock mixing."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from ..common import UserError

BOOT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def metrics(path: Path) -> dict:
    def read(name):
        file = path / name
        return file.read_text(errors="replace") if file.is_file() else ""

    gpio = read("gpio.log")
    ready = next((line for line in gpio.splitlines() if line.startswith("POLIS_GPIO_READY ")), "")
    fields = dict(re.findall(r"(\w+)=([^\s]+)", ready))
    config = dict(re.findall(r"^(\w+)=(.*)$", read("firmware-config-str.txt"), re.M))
    kernel = config.get("kernel", "unknown")
    rows = [(float(time) / 1000, message) for time, message in
            re.findall(r"^(\d+\.\d+): (.*)$", read("firmware.log"), re.M)]
    arm = next((time for time, message in rows if "arm_loader: Starting ARM" in message), None)
    loaded = next((i for i, (_, message) in enumerate(rows)
                   if message.startswith(f"Loaded '{kernel}' ")), None)
    read_window = post_load = None
    if loaded is not None:
        before = next((time for time, message in reversed(rows[:loaded])
                       if re.fullmatch(r"brfs: File read: \d+ bytes", message)), None)
        if before is not None:
            read_window = rows[loaded][0] - before
        if arm is not None:
            post_load = arm - rows[loaded][0]
    version = read("firmware-version.txt")
    return dict(kernel=kernel, firmware="cutdown" if "(start_cd)" in version else
                "standard" if "(start)" in version else "unknown",
                first_firmware_s=rows[0][0] if rows else None, arm_s=arm,
                kernel_read_window_s=read_window, after_load_s=post_load,
                linux_ready_s=float(fields["uptime"]) if "uptime" in fields else None,
                uart_wait_ms=float(fields["uart_wait_ms"]) if "uart_wait_ms" in fields else None)


def discover(source: Path | None) -> list[Path]:
    roots = [source] if source else [*Path('/run/media').glob('*/*/boot-diagnostics'),
                                   *Path('/media').glob('*/*/boot-diagnostics')]
    captures = []
    for root in roots:
        if root is None or not root.is_dir():
            continue
        paths = [root] if BOOT_ID.fullmatch(root.name) else list(root.iterdir())
        captures.extend(path for path in paths if BOOT_ID.fullmatch(path.name)
                        and path.is_dir() and not path.is_symlink()
                        and any((path / name).is_file() for name in ('boot.log', 'gpio.log')))
    return sorted(set(path.resolve() for path in captures))


def capture_hashes(path: Path) -> dict:
    return {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(path.iterdir()) if file.is_file()
            and not file.is_symlink() and file.suffix in ('.log', '.txt')}


def import_capture(source: Path, archive: Path, label: str | None) -> Path:
    destination = archive / source.name
    hashes = capture_hashes(source)
    if destination.exists():
        recorded = json.loads((destination / 'capture.json').read_text())
        if recorded['sha256'] != hashes or capture_hashes(destination) != hashes:
            raise UserError(f'Capture {source.name} changed; refusing to overwrite its archive.')
        if label and recorded['label'] != label:
            raise UserError(f'Capture {source.name} is already labelled {recorded["label"]!r}.')
        return destination
    archive.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.import-', dir=archive) as temp:
        pending = Path(temp) / source.name
        pending.mkdir()
        for name in hashes:
            shutil.copyfile(source / name, pending / name)
        if capture_hashes(pending) != hashes or capture_hashes(source) != hashes:
            raise UserError(f'Capture {source.name} changed during import; retry after logs finish.')
        data = dict(label=label or source.name[:8], source=str(source), sha256=hashes,
                    imported_utc=datetime.now(timezone.utc).isoformat(), metrics=metrics(pending))
        (pending / 'capture.json').write_text(json.dumps(data, indent=2) + '\n')
        pending.rename(destination)
    return destination


def report(archive: Path, source: Path | None = None, label: str | None = None) -> None:
    captures = discover(source)
    if source and not captures:
        raise UserError(f'No complete boot capture found at {source}.')
    if label and len(captures) != 1:
        raise UserError('--label requires exactly one mounted capture; use --source to select it.')
    for capture in captures:
        saved = import_capture(capture, archive, label)
        print(f'Archived: {saved}')
    rows = []
    for capture in sorted(archive.glob('*/capture.json')):
        data = json.loads(capture.read_text())
        if capture_hashes(capture.parent) != data['sha256']:
            raise UserError(f'Archived capture changed: {capture.parent}')
        rows.append(data)
    if not rows:
        raise UserError('No captures found. Mount polis-data or pass --source PATH.')
    print('\nCapture / firmware / kernel | first FW | read window | after load | ARM start | Linux ready')
    def fmt(value):
        return 'n/a' if value is None else f'{value:.3f}s'
    for row in sorted(rows, key=lambda data: data['imported_utc']):
        m = row['metrics']
        values = ' | '.join(fmt(m[key]) for key in ('first_firmware_s', 'kernel_read_window_s',
                                                   'after_load_s', 'arm_s', 'linux_ready_s'))
        print(f'{row["label"]} / {m["firmware"]} / {m["kernel"]} | {values}')
    print('\nFirmware and Linux use different clock origins; do not add the columns.')
    print('Read window brackets a kernel read, not an isolated SD transfer measurement.')
    print('n/a means unavailable (expected for cut-down firmware), not zero seconds.')
    print('Cold-boot power-to-LED timing still requires an external measurement.')
