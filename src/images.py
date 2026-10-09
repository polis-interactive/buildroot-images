"""Image discovery and confirmed whole-disk flashing on Linux."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from .common import Context, UserError, choose, run
from . import snapshots


EXPANSION_TOOLS = ("sfdisk", "blkid", "blockdev", "udevadm", "e2fsck", "resize2fs", "dumpe2fs")


def data_layout(path: Path, size: int, privilege=()) -> dict:
    """Accept only three ordered primary MBR partitions, with data in slot 3."""
    table = json.loads(run([*privilege, "sfdisk", "--json", path], capture=True).stdout)["partitiontable"]
    parts = table.get("partitions", [])
    if (table.get("label") != "dos" or table.get("unit") != "sectors"
            or table.get("sectorsize") != 512 or len(parts) != 3):
        raise UserError("--expand-data requires a 512-byte-sector MBR image with boot, root and data partitions.")
    end = 1
    separator = "p" if str(path)[-1].isdigit() else ""
    for number, part in enumerate(parts, 1):
        if (part.get("node") != f"{path}{separator}{number}"
                or part["start"] < end or part["size"] <= 0
                or part["type"].lower() not in (("b", "c", "0b", "0c") if number == 1 else ("83",))):
            raise UserError("--expand-data requires ordered, non-overlapping boot/root/data primary partitions.")
        end = part["start"] + part["size"]
    if end * 512 > size:
        raise UserError("Partition layout extends beyond the image or destination.")
    return table


def check_data_filesystem(path: Path, part: dict, privilege=()) -> None:
    result = run([*privilege, "blkid", "--probe", "--offset", str(part["start"] * 512),
                  "--size", str(part["size"] * 512), "--output", "export", path], capture=True)
    tags = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if tags.get("TYPE") != "ext4" or tags.get("LABEL") != "polis-data":
        raise UserError("--expand-data requires partition 3 to be ext4 with label 'polis-data'.")


def layout_signature(table: dict) -> dict:
    # Device paths differ between the source image and the destination card.
    return {key: ([{k: v for k, v in part.items() if k != "node"}
                   for part in value] if key == "partitions" else value)
            for key, value in table.items() if key != "device"}


def grow_data(device: Path, identity: tuple, image_size: int, source: dict,
              privilege: list[str]) -> None:
    """Grow the flashed partition in place, preserving its filesystem contents."""
    def recheck():
        if inspect_destination(device, image_size) != identity:
            raise UserError("Destination changed during expansion; stopped.")

    recheck()
    # dd replaced the table; refresh kernel partition devices before using them.
    run([*privilege, "blockdev", "--rereadpt", device])
    run([*privilege, "udevadm", "settle", "--timeout=10"])
    recheck()
    current = data_layout(device, identity[2], privilege)
    if layout_signature(current) != layout_signature(source):
        raise UserError("Flashed partition table differs from the source; expansion stopped.")
    part = current["partitions"][2]
    check_data_filesystem(device, part, privilege)
    sectors = identity[2] // 512 - part["start"]
    if sectors != part["size"]:
        run([*privilege, "sfdisk", "--wipe", "never", "--wipe-partitions", "never",
             "-N", "3", device], input=f"size={sectors}\n")
        run([*privilege, "blockdev", "--rereadpt", device])
        run([*privilege, "udevadm", "settle", "--timeout=10"])
    recheck()
    expected = layout_signature(source)
    expected["partitions"][2]["size"] = sectors
    expanded = data_layout(device, identity[2], privilege)
    if layout_signature(expanded) != expected:
        raise UserError("Unexpected partition table after expansion; filesystem resize stopped.")
    partition = Path(expanded["partitions"][2]["node"])
    if int(run([*privilege, "blockdev", "--getsize64", partition], capture=True).stdout) != sectors * 512:
        raise UserError("Kernel has not accepted the new data partition size; filesystem resize stopped.")
    recheck()
    try:
        run([*privilege, "e2fsck", "-f", "-p", partition])
    except subprocess.CalledProcessError as error:
        # e2fsck uses status 1 for successfully corrected errors.
        if error.returncode != 1:
            raise
    run([*privilege, "resize2fs", partition])
    header = run([*privilege, "env", "LC_ALL=C", "dumpe2fs", "-h", partition], capture=True).stdout
    fields = dict(line.split(":", 1) for line in header.splitlines() if ":" in line)
    block_size = int(fields["Block size"].strip())
    filesystem_size = int(fields["Block count"].strip()) * block_size
    if not 0 <= sectors * 512 - filesystem_size < block_size:
        raise UserError("Data filesystem did not grow to the partition size.")
    run([*privilege, "blockdev", "--flushbufs", device])
    print(f"/data expanded and flushed: {filesystem_size:,} bytes on {partition}.")


def discover_images(ctx: Context, output: Path | None = None) -> list[Path]:
    roots = [ctx.output(board) for board in ctx.boards]
    roots.extend(ctx.snapshots / name for name in snapshots.completed(ctx))
    if output is not None:
        roots.append(output)
    images = set()
    for root in roots:
        path = root / "images/sdcard.img"
        if (path.is_file() and path.stat().st_size > 0
                and not (root / ".incomplete").exists()):
            images.add(path.resolve())
    return sorted(images)


def list_images(ctx: Context, output: Path | None = None) -> None:
    images = discover_images(ctx, output)
    for index, image in enumerate(images, 1):
        print(f"{index:2}. {image} ({image.stat().st_size:,} bytes)")
    if not images:
        print("No completed sdcard.img files found. Build an image or supply --image.")


def disks(device: Path | None = None) -> list[dict]:
    args = ["lsblk", "--json", "--bytes", "--paths", "--exclude", "7",
            "--output", "NAME,TYPE,SIZE,RM,RO,TRAN,MODEL,SERIAL,MOUNTPOINTS"]
    if device is not None:
        args += ["--", str(device)]
    return json.loads(run(args, capture=True).stdout)["blockdevices"]


def show_disks(nodes: list[dict], depth=0) -> None:
    for node in nodes:
        mounts = ", ".join(m for m in node.get("mountpoints", []) or [] if m)
        flags = " read-only" if node.get("ro") else ""
        print(f"{'  ' * depth}{node['name']}  {node['type']}  "
              f"{int(node['size']):,} bytes  {(node.get('model') or '').strip()}"
              f"{flags}  {mounts}")
        show_disks(node.get("children", []), depth + 1)


def validate_disk(node: dict, image_size: int) -> None:
    if node["type"] != "disk":
        raise UserError("Choose a whole disk, not a partition or mapped device.")
    if node.get("ro"):
        raise UserError("The destination is read-only.")
    pending = [node]
    while pending:
        child = pending.pop()
        if any(child.get("mountpoints") or []):
            raise UserError("The disk has mounted filesystems or active swap. Unmount them first.")
        pending.extend(child.get("children", []))
    if int(node["size"]) < image_size:
        raise UserError("The image is larger than the destination disk.")


def inspect_destination(device: Path, image_size: int) -> tuple:
    info = device.stat()
    if not stat.S_ISBLK(info.st_mode):
        raise UserError(f"Not a block device: {device}")
    nodes = disks(device)
    if len(nodes) != 1 or Path(nodes[0]["name"]).resolve() != device:
        raise UserError("Could not uniquely identify the destination disk.")
    node = nodes[0]
    validate_disk(node, image_size)
    return info.st_rdev, node.get("serial"), int(node["size"])


def flash(ctx: Context, image: Path | None, device: Path | None,
          output: Path | None = None, *, expand_data: bool = False) -> None:
    if image is None:
        image = choose(discover_images(ctx, output), "Image")
        if image is None:
            return
    if not sys.stdin.isatty():
        raise UserError("Flashing requires an interactive terminal for confirmation.")
    image = image.resolve(strict=True)
    if not image.is_file() or image.stat().st_size == 0:
        raise UserError("Choose a nonempty image file.")
    if (image.parent.parent / ".incomplete").exists():
        raise UserError("This snapshot is incomplete.")
    image_identity = image.stat()
    layout = None
    if expand_data:
        missing = [tool for tool in EXPANSION_TOOLS if shutil.which(tool) is None]
        if missing:
            raise UserError(f"--expand-data requires these host tools: {', '.join(missing)}")
        layout = data_layout(image, image_identity.st_size)
        check_data_filesystem(image, layout["partitions"][2])
    nodes = disks()
    show_disks(nodes)
    if device is None:
        # Whole disks are offered explicitly; mounted disks are still refused below.
        selected = choose([n["name"] for n in nodes if n["type"] == "disk"], "Device")
        if selected is None:
            return
        device = Path(selected)
    device = device.resolve(strict=True)
    identity = inspect_destination(device, image_identity.st_size)
    if expand_data and identity[2] // 512 > 2**32:
        raise UserError("--expand-data supports MBR destinations up to 2 TiB.")
    print(f"\nImage: {image}\nDestination: {device}\nAll data on this disk will be overwritten.")
    if expand_data:
        size = identity[2] - layout["partitions"][2]["start"] * 512
        print(f"After flashing, expand /data to fill the card (approximately {size:,} bytes).")
    if input(f"Type {device} to confirm: ").strip() != str(device):
        raise UserError("Cancelled; no data written.")
    privilege = []
    if os.geteuid() != 0:
        run(["sudo", "-v"])
        privilege = ["sudo"]
    if inspect_destination(device, image_identity.st_size) != identity:
        raise UserError("Destination changed; no data written.")
    # Source probes may update atime; only identity/content changes invalidate it.
    current_image = image.stat()
    if any(getattr(current_image, field) != getattr(image_identity, field)
           for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")):
        raise UserError("Image changed; no data written.")
    if expand_data:
        if int(run([*privilege, "blockdev", "--getss", device], capture=True).stdout) != 512:
            raise UserError("--expand-data requires a destination with 512-byte logical sectors; no data written.")
    run([*privilege, "dd", f"if={image}", f"of={device}",
         "bs=4M", "conv=fsync", "status=progress"])
    if expand_data:
        try:
            grow_data(device, identity, image_identity.st_size, layout, privilege)
        except (UserError, OSError, ValueError, subprocess.CalledProcessError) as error:
            raise UserError(f"Image was written, but /data expansion failed: {error}. "
                            "Do not use the card until expansion succeeds or it is reflashed.") from error
    print(f"Image written and flushed to {device}.")
