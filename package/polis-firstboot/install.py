#!/usr/bin/env python3
"""Install mode-specific setup after overlays, before root filesystem creation."""
import json
import os
from pathlib import Path
import shutil
import sys


def install(target, plan):
    enabled = not plan['development']
    destination = target / 'usr/share/polis-firstboot'
    if enabled:
        for name in ('rpi-eeprom-config', 'pieeprom.bin'):
            if not (destination / name).is_file():
                raise RuntimeError(f'Missing first-boot dependency: {name}')
        shutil.copy2(Path(__file__).with_name('firstboot.py'), destination / 'firstboot.py')
    elif destination.exists():
        # Normally rejected by output ownership first; also guard direct hooks.
        shutil.rmtree(destination)
    config = target / 'etc/polis-firstboot.json'
    config.write_text(json.dumps(dict(schema=1, enabled=enabled, settings=plan['eeprom'],
                                     plan_sha256=plan['fingerprint'])) + '\n')
    service = target / 'etc/init.d/polis-firstboot'
    service.write_text('''#!/bin/sh
# Called after /data is mounted, before services and readiness.
set -eu
''' + ('''exec /usr/bin/python3 -B /usr/share/polis-firstboot/firstboot.py
''' if enabled else '''printf '%s\\n' '{"schema":1,"enabled":false,"status":"disabled","activity":false,"benchmark_eligible":true}' > /run/polis-provisioning.json
'''))
    service.chmod(0o755)


if __name__ == '__main__':
    install(Path(sys.argv[1]), json.loads((Path(os.environ['BASE_DIR']) / 'profile/image-plan.json').read_text()))
