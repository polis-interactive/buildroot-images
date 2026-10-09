#!/usr/bin/env python3
"""Install the common BusyBox end-of-startup probe in every board profile."""
from pathlib import Path
import shutil
import sys
import re


def install(target: Path) -> None:
    inittab = target / "etc/inittab"
    lines = inittab.read_text().splitlines()
    startup = "/etc/init.d/rcS"
    wrapper = "/usr/libexec/polis-smoke-rcS"
    entries = [i for i, line in enumerate(lines)
               if ":sysinit:" in line and not line.lstrip().startswith("#")
               and (startup in line or wrapper in line)]
    if len(entries) != 1:
        raise RuntimeError(f"Expected one BusyBox rcS startup entry in {inittab}")
    # Replace only the rcS command; preserve any mount-data && rcS guard.
    index = entries[0]
    lines[index] = re.sub(r"/etc/init\.d/rcS(?=$|[\s'\"])", wrapper, lines[index])
    destination = target / wrapper.lstrip("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("smoke-init-finished.sh"), destination)
    destination.chmod(0o755)
    inittab.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    install(Path(sys.argv[1]))
