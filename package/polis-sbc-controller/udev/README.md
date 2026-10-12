# Host device permissions

`70-polis-sbc-controller.rules` is the tracked access policy for Linux hosts. Install it
from the repository root with:

```sh
make setup-permissions
# Or grant access to a different non-root daemon account:
make setup-permissions SBC_CONTROLLER_USER=account
```

The target invokes sudo. Its installer creates the `polis-sbc-controller` system group,
adds the selected user without replacing their existing groups, installs the
rules under `/etc/udev/rules.d/`, and reloads udev's rule definitions. Log out and
back in, restart the daemon, and reconnect devices while the fixture is idle.
Installation does not trigger device events, change power, or write to storage.
`make setup` installs downloaded tools separately and does not grant permissions.

| Device | Match | Access |
| --- | --- | --- |
| Pi 4 ROM loader | USB `0a5c:2711` | Root and `polis-sbc-controller`, mode `0660` |
| Pico control channel | USB `c0de:cafe`, product `Polis SBC Controller`, ACM tty | Root and `polis-sbc-controller`, mode `0660` |
| Pi diagnostics | Product `Polis_Pi_Diagnostics`, ACM tty | Root and `polis-sbc-controller`, mode `0660` |
| RAM recovery microSD | USB `0a5c:0104`, vendor `mmcblk0`, disk type, instance `0:0` | Whole disk: root and `polis-sbc-controller`, mode `0660`; disk and partitions: `UDISKS_IGNORE=1` |

The storage identity matches the commissioned official recovery gadget's SD LUN.
Other USB disks and other LUNs do not match. The standard persistent-storage rules
populate the disk properties and propagate them to partitions before this file
runs. No host username, physical port or individual device serial appears here.

Membership grants direct access to **all matching devices on the host**. It is
separate from fixture selection: the daemon discovers the Pico by USB product
name; recovery still requires the configured Pi USB topology, SD serial and LUN path.
Moving the Pi to a new port still requires updating and qualifying that fixture
configuration. Pico BOOTSEL/picotool permissions remain managed by picotool's
own host rules.

The installer replaces the old `70-polis-rpiboot.rules`,
`70-polis-recovery-storage.rules`, and `99-polis-tester.rules`. It first preserves
their contents in hash-named `.disabled` files beside them. Those backups are
not loaded by udev. A changed version of the new rule receives the same backup
treatment. Repeated installation is supported; symlink-managed rules require
explicit administrator reconciliation. Existing permissions on connected
devices remain until those devices reconnect.

Check syntax without sudo or installing anything:

```sh
python3 -m src.setup.permissions --check
```

The installer requires `udevadm verify` (systemd 254 or newer), `groupadd`, and
`usermod`. Rules use final owner/group/mode assignments so later rules cannot
replace their access policy. See the upstream
[udev rule reference](https://www.freedesktop.org/software/systemd/man/latest/udev.html).
