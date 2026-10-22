#!/usr/bin/env python3
"""Pi 4 setup. No network, OTP programming, firmware upgrades, or automatic retries."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

FIRMWARE_VERSION = '57db150d63864d47e6c9071f6b086a5401eb4e92'
FIRMWARE_SHA256 = '3bf21d52b020a7e76a5fe9903e714a7fb5272c0a3a7bc1c1410f9cf45ed8b933'
ASSETS = Path('/usr/share/polis-firstboot')
DATA = Path('/dev/mmcblk0p3')
BOOT = Path('/run/polis-boot')
STATUS = Path('/run/polis-provisioning.json')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(path, data):
    atomic(path, (json.dumps(data, sort_keys=True, indent=2) + '\n').encode())


def command(*args):
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True,
                          text=True, timeout=120, env={**os.environ, 'LC_ALL': 'C'}).stdout


def active(text):
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def merge_config(before, settings):
    """Preserve unrelated lines verbatim; refuse ambiguous conditional overrides."""
    require(bool(before.strip()), 'Empty current EEPROM configuration')
    for key, value in settings.items():
        require(re.fullmatch('[A-Z][A-Z0-9_]*', key) and
                re.fullmatch('[a-zA-Z0-9_.,:/+ -]+', value), 'Invalid EEPROM setting')
    section, current, kept = 'all', {}, []
    for line in before.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            require(re.fullmatch(r'\[[^\[\]]+\]', stripped), 'Malformed EEPROM section')
            section = stripped[1:-1]
        key, separator, value = stripped.partition('=')
        if separator and key.strip() in settings:
            require(section == 'all', 'Requested setting has a conditional EEPROM override')
            current[key.strip()] = value.strip()
        else:
            kept.append(line)
    if all(current.get(key) == value for key, value in settings.items()):
        return before
    return '\n'.join(kept).rstrip() + '\n[all]\n' + ''.join(
        f'{key}={value}\n' for key, value in sorted(settings.items()))


class Target:
    def __init__(self):
        self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.activity = False

    def identity(self):
        model = Path('/sys/firmware/devicetree/base/model').read_bytes().rstrip(b'\0')
        require(model.startswith(b'Raspberry Pi 4 Model B'), 'Only Raspberry Pi 4 Model B is supported')
        serial = Path('/sys/firmware/devicetree/base/serial-number').read_bytes().decode().strip('\0\n')
        require(re.fullmatch('[0-9a-fA-F]+', serial), 'Missing board serial')
        require(os.stat('/data').st_dev == os.stat(DATA).st_rdev, '/data is not mounted from SD partition 3')
        require(os.stat('/data').st_dev != os.stat('/').st_dev, '/data must be separate from root')
        return serial

    def config(self):
        return command('vcgencmd', 'bootloader_config')

    def version(self):
        return command('vcgencmd', 'bootloader_version')

    def prepare(self, directory, before, after, version):
        require(FIRMWARE_VERSION in version, 'Installed EEPROM release is not bundled; no firmware upgrade attempted')
        require(sha((ASSETS / 'pieeprom.bin').read_bytes()) == FIRMWARE_SHA256, 'EEPROM firmware hash mismatch')
        require(not re.search(r'^\s*(?:ENABLE_SELF_UPDATE=0|SIGNED_BOOT=1|FREEZE_VERSION=1)\s*$', before, re.M),
                'EEPROM self-update is disabled, frozen, or signed')
        match = re.search(r'update-time\s+(\d+)', version)
        require(match is not None, 'Missing EEPROM update timestamp')
        timestamp = max(int(time.time()), int(match[1]) + 1)
        require(timestamp < 0xffffffff, 'Invalid EEPROM update timestamp')
        atomic(directory / 'before.conf', before.encode())
        atomic(directory / 'after.conf', after.encode())
        atomic(directory / 'before-version.txt', version.encode())
        for config, output in [('before.conf', 'rollback.bin'), ('after.conf', 'pieeprom.upd')]:
            update_time = timestamp + 1 if output == 'rollback.bin' else timestamp
            command('/usr/bin/python3', '-B', ASSETS / 'rpi-eeprom-config', '--config', directory / config,
                    '--timestamp', str(update_time), '--out', directory / output, ASSETS / 'pieeprom.bin')
            extracted = command('/usr/bin/python3', '-B', ASSETS / 'rpi-eeprom-config', directory / output)
            require(active(extracted) == active((directory / config).read_text()), 'EEPROM configuration round-trip failed')
        payload = (directory / 'pieeprom.upd').read_bytes()
        atomic(directory / 'pieeprom.sig', f'{sha(payload)}\nts: {timestamp}\ntarget-soc: 2711\n'.encode())
        rollback_hash = sha((directory / 'rollback.bin').read_bytes())
        atomic(directory / 'rollback.sig', f'{rollback_hash}\nts: {timestamp + 1}\ntarget-soc: 2711\n'.encode())
        save(directory / 'recovery.json', dict(source_sha256=FIRMWARE_SHA256, version=FIRMWARE_VERSION,
             rollback_sha256=rollback_hash, timestamp=timestamp,
             note='Rollback is reconstructed same-release firmware with saved configuration, not an EEPROM dump.'))
        os.sync()
        return {name: sha((directory / name).read_bytes()) for name in ('pieeprom.upd', 'pieeprom.sig')}

    @contextmanager
    def boot(self):
        BOOT.mkdir(exist_ok=True)
        command('mount', '-t', 'vfat', '-o', 'rw,nosuid,nodev,noexec', '/dev/mmcblk0p1', BOOT)
        try:
            yield BOOT
        finally:
            command('umount', BOOT)

    def stage(self, directory, hashes):
        with self.boot() as boot:
            for name in ('pieeprom.upd', 'pieeprom.sig', 'pieeprom.bin', 'recovery.bin', 'recovery.000', 'vl805.bin', 'vl805.sig'):
                require(not (boot / name).exists(), f'Foreign update file on boot partition: {name}')
            # The signature is the commit point. Flush the complete image first.
            for name in ('pieeprom.upd', 'pieeprom.sig'):
                payload = (directory / name).read_bytes()
                require(sha(payload) == hashes[name], 'Prepared update changed')
                atomic(boot / name, payload)
            os.sync()

    def cleanup(self, hashes):
        with self.boot() as boot:
            # Validate both before removing either; never delete someone else's update.
            for name, digest in hashes.items():
                path = boot / name
                require(not path.exists() or sha(path.read_bytes()) == digest, f'Foreign update file: {name}')
            for name in ('pieeprom.sig', 'pieeprom.upd'):
                (boot / name).unlink(missing_ok=True)
            os.sync()

    def publish(self, report):
        save(STATUS, report)
        print('POLIS_PROVISIONING ' + json.dumps(report, sort_keys=True), flush=True)

    def reboot(self):
        os.sync()
        command('reboot')
        # BusyBox init processes reboot signals AFTER its sysinit child exits.
        # A nonzero exit also stops mount-data and its && rcS continuation.
        raise SystemExit(75)


def provision(target, config, root):
    report = dict(schema=1, enabled=config['enabled'], boot_id=target.boot_id,
                  status='disabled', activity=False, benchmark_eligible=True)
    if not config['enabled']:
        target.publish(report)
        return report
    report.update(status='running', benchmark_eligible=False)
    target.publish(report)
    try:
        serial = target.identity()
        settings = config['settings']
        if settings:
            key = sha(json.dumps(settings, sort_keys=True).encode())
            directory = root / serial / key
            directory.mkdir(parents=True, exist_ok=True)
            state_path = directory / 'state.json'
            state = json.loads(state_path.read_text()) if state_path.exists() else {'phase': 'new'}
            phase = state['phase']
            if phase == 'failed':
                raise RuntimeError('Previous EEPROM attempt failed; inspect recovery records before retrying')
            if phase == 'attempted':
                target.activity = True
                if state['boot_id'] == target.boot_id:
                    raise RuntimeError('EEPROM attempt already made this boot; refusing another reboot')
                # Clear our files even after a failed/partial update, before verification.
                target.cleanup(state['hashes'])
                before = target.config()
                verified = (active(before) == active((directory / 'after.conf').read_text()) and
                            FIRMWARE_VERSION in target.version())
                state['phase'] = 'complete' if verified else 'failed'
                save(state_path, state)
                require(verified, 'EEPROM verification failed after reboot; automatic retry disabled')
                atomic(directory / 'verified.conf', before.encode())
                report['eeprom'] = 'verified'
            elif phase == 'complete':
                current = target.config()
                require(merge_config(current, settings) == current,
                        'EEPROM settings changed after provisioning; inspect records before re-arming')
                report['eeprom'] = 'complete'
            else:
                require(phase == 'new', 'Unknown EEPROM provisioning state')
                target.activity = True
                before = target.config()
                after = merge_config(before, settings)
                if after == before:
                    save(state_path, dict(phase='complete', result='already-matched'))
                    report['eeprom'] = 'already-matched'
                else:
                    target.activity = True
                    try:
                        hashes = target.prepare(directory, before, after, target.version())
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                        save(state_path, dict(phase='failed', error=str(error)))
                        raise
                    # Persist intent BEFORE touching FAT. A crash consumes the attempt;
                    # the next boot cleans up and verifies, never silently re-stages.
                    save(state_path, dict(phase='attempted', boot_id=target.boot_id, hashes=hashes))
                    target.stage(directory, hashes)
                    report.update(status='reboot-required', eeprom='staged', activity=True)
                    target.publish(report)
                    save(root / 'result.json', report)
                    target.reboot()
                    return report
        else:
            report['eeprom'] = 'not-requested'
        report.update(status='complete', activity=target.activity,
                      benchmark_eligible=not target.activity)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report.update(status='failed', error=str(error), activity=target.activity, benchmark_eligible=False)
    target.publish(report)
    save(root / 'result.json', report)
    return report


def main():
    os.umask(0o077)
    target = Target()
    with open('/run/polis-firstboot.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = json.loads(Path('/etc/polis-firstboot.json').read_text())
        provision(target, config, Path('/data/provisioning'))
    # Failures are reported separately; normal services remain available for recovery.


if __name__ == '__main__':
    main()
