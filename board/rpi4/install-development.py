#!/usr/bin/env python3
"""Install development state links and public authorization; never private keys."""
import os
import json
from pathlib import Path
import sys


def install(target, profile):
    for name, destination in (("etc/dropbear", "/data/config/dropbear"),
                              ("root/.ssh", "/data/config/ssh")):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            raise RuntimeError(f"Refusing to replace unexpected state directory: {path}")
        path.symlink_to(destination)
    # Buildroot normally points this at /tmp; keep machine identity persistent.
    dbus = target / "var/lib/dbus"
    if dbus.is_symlink():
        dbus.unlink()
    dbus.mkdir(parents=True, exist_ok=True)
    key = (profile / "authorized_keys").read_text()
    destination = target / "usr/share/polis-development/authorized_keys"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(key)
    destination.chmod(0o644)
    image = json.loads((profile / 'image-plan.json').read_text())
    (target / 'etc/polis-development-image.json').write_text(json.dumps(dict(
        profile=image['profile'], plan_sha256=image['fingerprint']), indent=2) + '\n')


if __name__ == "__main__":
    install(Path(sys.argv[1]), Path(os.environ["BASE_DIR"]) / "profile")
