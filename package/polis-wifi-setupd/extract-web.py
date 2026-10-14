#!/usr/bin/env python3
"""Validate a release portal archive before extracting its allowlisted files."""
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

SUFFIXES = {'.css', '.js', '.wasm', '.svg', '.png', '.ico', '.woff', '.woff2'}


def extract(archive, destination):
    entries = []
    files = set()
    with tarfile.open(archive, 'r:gz') as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name:
                raise ValueError('Unsafe portal archive path')
            if member.isdir() and (path == PurePosixPath('.') or path.parts[0] == 'assets'):
                continue
            if (not member.isfile() or path in files or not
                    (path == PurePosixPath('index.html') or
                     (len(path.parts) > 1 and path.parts[0] == 'assets' and path.suffix in SUFFIXES))):
                raise ValueError(f'Unexpected portal archive entry: {member.name}')
            files.add(path)
            entries.append((member, path))
        if PurePosixPath('index.html') not in files or not any(p.suffix == '.wasm' for p in files):
            raise ValueError('Portal archive needs index.html and compiled WASM under assets/')
        for path in files:
            if any(parent in files for parent in path.parents):
                raise ValueError('Portal archive has conflicting file/directory paths')
        # Reconfiguration refreshes only this package's generated web directory.
        if destination.is_symlink():
            raise ValueError('Portal destination must not be a symlink')
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, mode=0o755)
        for member, relative in entries:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with source.extractfile(member) as input_file, path.open('wb') as output_file:
                shutil.copyfileobj(input_file, output_file)
            path.chmod(0o644)


if __name__ == '__main__':
    extract(Path(sys.argv[1]), Path(sys.argv[2]))
