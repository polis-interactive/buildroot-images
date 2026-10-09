#!/usr/bin/env python3
"""Start the stateless GPIO probe before persistent storage and rcS."""
from pathlib import Path
import sys


def install(target: Path) -> None:
    if not (target / "usr/bin/polis-gpio-test").is_file():
        return
    inittab = target / "etc/inittab"
    service = "/etc/init.d/polis-gpio-test"
    lines = [line for line in inittab.read_text().splitlines()
             if service not in line]
    mounts = [i for i, line in enumerate(lines)
              if line == "::sysinit:/bin/mount -a"]
    shutdowns = [i for i, line in enumerate(lines)
                 if ":shutdown:" in line and not line.lstrip().startswith("#")]
    if len(mounts) != 1 or not shutdowns or shutdowns[0] <= mounts[0]:
        raise RuntimeError(
            f"Expected mount -a and shutdown entries in {inittab}")
    lines.insert(shutdowns[0], f"null::shutdown:{service} stop")
    lines.insert(mounts[0] + 1, f"null::sysinit:{service} start")
    inittab.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    install(Path(sys.argv[1]))
