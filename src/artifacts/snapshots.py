"""Named, non-overwriting snapshots of completed builds."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
from zoneinfo import ZoneInfo

from ..common import Context, UserError, read_config, run


def automatic_name(ctx: Context, profile: str) -> str:
    day = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d")
    base = f"{profile}-{day}"
    name = base
    number = 2
    while (ctx.snapshots / name).exists() or (ctx.snapshots / name).is_symlink():
        name = f"{base}-{number}"
        number += 1
    return name


def completed(ctx: Context) -> list[str]:
    """Discover named, finished captures only; build outputs are not snapshots."""
    return sorted(path.name for path in ctx.snapshots.glob("*")
                  if path.is_dir() and not path.is_symlink()
                  and not (path / ".incomplete").exists()
                  and all((path / name).is_file() for name in
                          ("metadata.txt", "SHA256SUMS", "configs/buildroot.config", "images/sdcard.img"))
                  and (path / "images/sdcard.img").stat().st_size > 0)


def source_for(ctx: Context, name: str) -> Path:
    if name not in completed(ctx):
        raise UserError(f"No completed snapshot {name!r} in {ctx.snapshots}. "
                        "Use 'smoke' to list snapshots, or build/snapshot an image first.")
    return (ctx.snapshots / name).resolve()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checksum_line(path: Path, root: Path) -> str:
    name = path.relative_to(root).as_posix()
    escaped = "\\" in name or "\n" in name
    name = name.replace("\\", "\\\\").replace("\n", "\\n")
    return ("\\" if escaped else "") + f"{digest(path)}  {name}\n"


def destination_for(ctx: Context, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise UserError("Snapshot names may contain letters, numbers, dots, underscores, and hyphens.")
    destination = ctx.snapshots / name
    if destination.exists() or destination.is_symlink():
        raise UserError(f"Snapshot {name!r} already exists; choose a new name.")
    return destination


def snapshot(ctx: Context, source: Path, name: str) -> Path:
    destination = destination_for(ctx, name)
    source = source.resolve(strict=True)
    image = source / "images/sdcard.img"
    if not image.is_file() or image.stat().st_size == 0:
        raise UserError(f"No completed sdcard.img in {source}.")
    if (source / ".incomplete").exists():
        raise UserError("Cannot snapshot an incomplete capture.")
    config = source / ".config"
    if not config.is_file():
        raise UserError(f"Missing {config}. Capture a configured output directory.")
    if destination.resolve().is_relative_to((source / "images").resolve()):
        raise UserError("Snapshots must be stored outside the source images directory.")
    ctx.snapshots.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
    except FileExistsError:
        raise UserError(f"Snapshot {name!r} already exists; choose a new name.") from None
    marker = destination / ".incomplete"
    marker.touch()
    # Failures intentionally leave this marker; discovery never offers partial captures.
    print("Snapshot 1/5: hashing source SD image...", flush=True)
    original_hash = digest(image)
    # Dereference links so every saved artifact is independent of the build output.
    print("Snapshot 2/5: copying images and configs...", flush=True)
    shutil.copytree(source / "images", destination / "images", symlinks=False)
    if (source / 'profile/audit.json').is_file():
        shutil.copytree(source / 'profile', destination / 'profile', symlinks=False)
    from ..profiles.provision import bundle_for
    bundle = bundle_for(source)
    if bundle:
        shutil.copytree(bundle, destination / bundle.relative_to(source), symlinks=False)
    configs = destination / "configs"
    configs.mkdir()
    shutil.copy2(config, configs / "buildroot.config")
    for pattern in ("linux-*/.config", "busybox-*/.config"):
        for path in (source / "build").glob(pattern):
            shutil.copy2(path, configs / f"{path.parent.name}.config")
    defconfig = read_config(config).get("BR2_DEFCONFIG")
    if defconfig and Path(defconfig).is_file():
        shutil.copy2(defconfig, configs / "saved_defconfig")
    shutil.copy2(ctx.buildroot / "Makefile", configs / "buildroot-Makefile")
    members = ("Makefile", "build.toml", "pyproject.toml", "uv.lock", ".python-version",
               "src", "Config.in", "external.desc", "external.mk",
               "profiles", "board", "build-support", "package", "patches", "README.md")
    def archive_filter(info):
        path = Path(info.name)
        if "__pycache__" in path.parts or info.name.endswith(".pyc"):
            return None
        # Prune Cargo output before tarfile recurses into gigabytes of binaries.
        # Preserve unrelated source directories that happen to be named target.
        if path.name in ("target", ".target") and (ctx.repo / path.parent / "Cargo.toml").is_file():
            return None
        return info
    print("Snapshot 3/5: archiving workspace sources (excluding Cargo target directories)...",
          flush=True)
    with tarfile.open(destination / "external-at-capture.tar.gz", "w:gz") as archive:
        for name_in_repo in members:
            path = ctx.repo / name_in_repo
            if path.exists():
                archive.add(path, arcname=name_in_repo, filter=archive_filter)
    captured = datetime.now(timezone.utc).isoformat()
    modified = datetime.fromtimestamp(image.stat().st_mtime, timezone.utc).isoformat()
    (destination / "metadata.txt").write_text(
        f"name={name}\ncaptured_utc={captured}\nsource_output={source}\n"
        f"buildroot={ctx.buildroot}\nsource_config={config}\nsource_image_mtime={modified}\n"
        "boot_time=not measured\n"
        "Capture only completed, idle builds. Configs and workspace are copied as found at capture time.\n")
    sizes = ["metric\tbytes"]
    for path in sorted((destination / "images").iterdir()):
        if path.is_file():
            sizes.append(f"{path.name}\t{path.stat().st_size}")
    rootfs = destination / "images/rootfs.ext4"
    if rootfs.exists() and shutil.which("dumpe2fs"):
        header = run(["dumpe2fs", "-h", rootfs], capture=True,
                     env={**os.environ, "LC_ALL": "C"}).stdout
        values = dict(re.findall(r"^(Block count|Free blocks|Block size):\s+(\d+)", header, re.M))
        used = (int(values["Block count"]) - int(values["Free blocks"])) * int(values["Block size"])
        sizes.append(f"rootfs_used_including_metadata\t{used}")
    (destination / "sizes.tsv").write_text("\n".join(sizes) + "\n")
    print("Snapshot 4/5: verifying copied SD image...", flush=True)
    if digest(image) != original_hash or digest(destination / "images/sdcard.img") != original_hash:
        raise UserError("Source image changed or copy failed; snapshot remains incomplete.")
    audit_path = source / 'profile/audit.json'
    if audit_path.is_file():
        for artifact in json.loads(audit_path.read_text()).get('artifacts', []):
            relative = Path(artifact['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise UserError('Invalid artifact path in saved audit')
            if (digest(source / relative) != artifact['sha256'] or
                    digest(destination / relative) != artifact['sha256']):
                raise UserError('Artifact changed or copy failed; snapshot remains incomplete.')
    files = sorted(p for p in destination.rglob("*") if p.is_file() and p != marker)
    print("Snapshot 5/5: checksumming saved artifacts...", flush=True)
    (destination / "SHA256SUMS").write_text("".join(checksum_line(p, destination) for p in files))
    marker.unlink()
    for path in destination.rglob("*"):
        path.chmod(path.stat().st_mode & ~0o222)
    destination.chmod(destination.stat().st_mode & ~0o222)
    print(f"Saved read-only snapshot: {destination}")
    print("\n".join(sizes))
    return destination
