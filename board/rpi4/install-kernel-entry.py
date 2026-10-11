"""Apply one checked Pi4 marker patch without a clean Linux rebuild."""
from pathlib import Path
import subprocess
import sys


def install(source: Path):
    patch = Path(__file__).with_name('kernel-entry.patch').resolve()
    args = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)]
    # A matching reverse dry-run proves the complete patch is installed; a
    # substring alone could silently accept a partially edited instrumentation.
    present = subprocess.run([*args, '--force', '--reverse', '--dry-run'], cwd=source,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if present.returncode == 0:
        print('Polis kernel-entry marker already installed; preserving Linux objects.')
        return
    subprocess.run([*args, '--forward', '--dry-run'], cwd=source, check=True)
    subprocess.run([*args, '--forward'], cwd=source, check=True)


if __name__ == '__main__':
    install(Path(sys.argv[1]))
