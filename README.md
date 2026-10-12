# Polis Buildroot images

Build Raspberry Pi 4 images from two declarative profiles:

| Profile | Contents |
| --- | --- |
| [`polis-rpi4-base`](profiles/polis-rpi4-base.toml) | musl toolchain, read-only EROFS root, writable ext4 data, enforced static core linking |
| [`polis-rpi4-speed`](profiles/polis-rpi4-speed.toml) | The base plus product services, diagnostics, capability restrictions, kernel/firmware optimizations, and EEPROM network-installer configuration |

```sh
make profiles
make image ARGS="--profile polis-rpi4-speed"
# Build-time experiments and hardware debugging: skip first-boot setup.
make image ARGS="--profile polis-rpi4-speed --development"
```

`image` configures, builds, assembles and verifies the SD image, and saves a
named snapshot. No connected Pi, running daemon, or saved diagnostics are required.
Final images enable first-boot EEPROM configuration and `/data` expansion by
default. `--development` skips both in an isolated build. The audit is always saved;
the automatic workflow prints its location without displaying all differences.
No profile is selected implicitly. Omit `--profile` to use the interactive picker.

Planned work is tracked in [TODO.md](TODO.md).

## Declarative build profiles

Edit `profiles/*.toml` to choose groups. Names are validated Python enums in
[`src/profiles/options.py`](src/profiles/options.py), with editor completion from
the generated JSON schema. Each `profiles/groups/*.toml` owns its settings and
supporting files. Group order does not establish precedence: conflicts fail,
and prerequisites must be selected explicitly.

```sh
# Staged configuration displays the audit before compilation.
make configure ARGS="--profile polis-rpi4-speed"
make build ARGS="--profile polis-rpi4-speed"
# Optional board-specific EEPROM preparation, using saved diagnostics:
make prepare-eeprom ARGS="--profile polis-rpi4-speed"
make snapshot ARGS="--profile polis-rpi4-speed --name my-speed-image"

# Read the saved audit at any time.
make audit ARGS="--profile polis-rpi4-speed"
```

The speed profile selects kernel trimming, LZ4 kernel loading, cut-down firmware
(`gpu_mem=16`), premerged device-tree overlays, a boot ramdisk, and disabled
EEPROM network-installer scanning. It also selects headless operation, no
Ethernet/input/32-bit/container support, local storage, and USB peripheral mode.
Camera/display use requires a different selection: remove the conflicting
restrictions and enable the drivers and userspace your product needs.

The base does not select boot-log collection, Wi-Fi, or diagnostic services. Retaining
a capability does not automatically install its drivers or applications.

Generated files and audits live in `output/<profile>-<input-hash>/profile/`.
Different inputs get separate build directories. Staged builds reject changed
inputs or hand-edited generated configuration. Use the same `--output PATH`
across configure/build/snapshot when overriding the default.

Requested changes carry ownership and input hashes. Buildroot, kernel and
BusyBox selections are checked against their effective configurations, and ELF
checks enforce static core linking. Full attribution of dependency-induced
configuration changes remains pending; audits state that limitation explicitly.
See [the profile and audit contract](docs/build-profiles.md).

## First-boot setup

Ordinary images expand `/data` to fill the SD card and apply the profile's EEPROM
settings on the Pi. This works with removable-SD and daemon-controlled flashing;
building requires no Pi, daemon, or saved diagnostics. Use `--development` on
`image`, or consistently on staged `configure`, `build`, `snapshot`, and `audit`
commands, to omit setup. The mode is included in the input hash, output directory,
automatic snapshot name, audit, and `images/provisioning.json`. Switching modes
with a reused `--output` is rejected. Development firmware also disables bootloader
self-update; it does not revert previously installed EEPROM settings.

Setup runs after mounting `/data`, before services. Expansion preserves the first
two partitions and existing files, uses online ext4 resize, and becomes a no-op
when the card is full. It also works when no EEPROM settings are selected.
`make flash ARGS="--expand-data"` remains available; setup detects prior expansion.

EEPROM setup reads the target's configuration and preserves unrelated settings.
When a change is needed it saves recovery files, stages an update, and reboots
once. The next boot removes the staged files and verifies the configuration.
Failed or interrupted updates are never automatically re-staged. Settings already
matching need no update. Setup currently supports Pi 4 Model B and changes on
the bundled `2025-11-05` EEPROM release (`57db150d63864d47e6c9071f6b086a5401eb4e92`).
Other releases can pass when settings already match; changes report unsupported
firmware instead of upgrading or downgrading it. Conditional overrides of a
requested key, signed/frozen firmware, and disabled self-update fail explicitly.

Read `/run/polis-provisioning.json` for this boot's status and
`/data/provisioning/result.json` for the persistent report. Recovery files live
under `/data/provisioning/<board-serial>/<settings-hash>/`: `before.conf`,
`before-version.txt`, `after.conf`, `rollback.bin`, `rollback.sig`, and
`recovery.json`. Rollback is reconstructed same-release firmware, not an EEPROM
dump. Do not delete `state.json` to retry until the failure and boot-partition
update files have been inspected. A rollback uses `rollback.bin` as `pieeprom.upd`
and `rollback.sig` as `pieeprom.sig` on the boot FAT; its timestamp is newer than
the staged update. Later EEPROM changes may require regenerating the rollback
with a newer timestamp. Applying rollback or re-arming setup is a manual operation.
The original partition table is saved under `/data/provisioning/storage/<card-id>/`.

Provisioning status is separate from normal boot readiness. Diagnostic captures
include `provisioning.txt`; benchmarks reject setup, verification, failed, or
missing-status boots. Use a development image for hardware iteration. Final
images include Python, the pinned Raspberry Pi EEPROM configuration tool,
`resize2fs`, and `partx`; development images omit these setup dependencies.
The daemon allows up to 600 seconds when it observes provisioning over UART;
expiry stops the workflow without automatically reflashing the card. QEMU smoke
timing requires a development snapshot.

## Optional capture-based EEPROM preparation

`make image` builds without capture-based EEPROM preparation. Use `make prepare-eeprom` for
the selected profile when you have saved board diagnostics:

```sh
make prepare-eeprom ARGS="--profile polis-rpi4-base"
# polis-rpi4-base: nothing to do; no EEPROM changes selected.

make prepare-eeprom ARGS="--profile polis-rpi4-speed"
```

The speed profile declares `NET_INSTALL_ENABLED=0`. Preparation uses its built
normal image and the latest saved diagnostics in `local/polis-sbc-controller-state/captures`.
Use `--capture DIRECTORY` on `prepare-eeprom` to select a capture. To include
EEPROM preparation in an image build and snapshot, explicitly pass
`make image ARGS="--profile polis-rpi4-speed --capture DIRECTORY"`.
Only that opt-in workflow validates capture evidence before the build starts.
Collect new diagnostics with `make sbc-controller-diagnostics` while the daemon
is running and the Pi is booted and connected. Ordinary image builds do not
collect or consult diagnostics; board EEPROM settings remain unverified in the audit.

If the saved configuration already matches, preparation records that result.
Otherwise it creates a separate update SD image, original configuration and
same-release rollback firmware. The selected bundle, evidence and preparation
result are hashed and included in the snapshot. A saved capture is historical
evidence; preparation does not certify the board's current state.

SD images contain no pre-staged EEPROM update files; final images prepare them
on the target only when needed. Applying a separate capture-based bundle remains
an explicit hardware operation. Deselecting a group does not revert EEPROM
settings already installed on a board. See the
[EEPROM preparation and application guide](board/rpi4/post-firmware/README.md).

## Build tools and incremental work

Use `make setup` to install the Python environment with uv and download/build
the pinned USB recovery and EEPROM tools. On Debian/Ubuntu, install the host
prerequisites first:

```sh
sudo apt-get install git build-essential pkg-config libusb-1.0-0-dev
make setup
```

Install uv separately if it is not already available. Setup downloads official
`raspberrypi/usbboot` commit `861a703d8b51f42b76bcdff49da3e0f2766966bf`
into ignored `local/usbboot/`, initializes its `rpi-eeprom` submodule at
`ded1e9332bd996f6adc46e10d22d9ef0298b1882`, and builds `rpiboot` locally.
The implementation and pins live in [`src/setup/usbboot.py`](src/setup/usbboot.py).
The first setup requires network access. Subsequent runs reuse the verified
checkout and rebuild the executable if needed. Edited checkouts or different
revisions are rejected rather than overwritten. Failed downloads/builds leave
no installed partial checkout.

Setup also prepares the verified RAM recovery payload in
`local/polis-sbc-controller-tools/gadget/`. Its config, checksum and recipe documentation
live in [`package/polis-sbc-controller/recovery/`](package/polis-sbc-controller/recovery/).
Both tool directories are reproducible with `make setup`. Setup does not flash
devices, provision EEPROM/OTP, or recreate device records or known-good snapshots.
`local/polis-recovery/` is now historical evidence, with no current runtime dependency.
UART logs and journals remain in `local/polis-sbc-controller-state/`.
`make clean` continues to remove only `.cache/` and `dl/`.
`make purge` removes all `output/` build directories, `local/usbboot/`, and
`local/polis-sbc-controller-tools/`; run it only when recovery tools are idle, then
run `make setup` afterward to restore the tools.

For SBC controller hardware access, run `make setup-permissions` separately. It uses sudo
to install the tracked [udev policy](package/polis-sbc-controller/udev/README.md)
and add your account to `polis-sbc-controller`. Use `SBC_CONTROLLER_USER=account` to select another
daemon user. Log out and back in before restarting the daemon; reconnect devices
while the fixture is idle. Permissions follow supported device types across USB
ports; the daemon retains its explicit fixture/port/serial selection checks.

`make` displays
commands; `make image help` displays command options. CLI arguments also work as
`make image -- --profile polis-rpi4-speed`.

| Option | Default |
| --- | --- |
| `--buildroot PATH` | `/home/broosegoose/Software/buildroot-2026.08` |
| `--downloads PATH` | the Buildroot tree's `dl/` directory |
| `--output PATH` | profile-specific output derived from input hashes |
| `--snapshots PATH` | this repository's `snapshots/` directory |
| `--name NAME` | for image/snapshot: profile plus date, with a unique suffix |

Keep build directories at their original paths: generated host tools contain
absolute paths. Different outputs share downloads but compile independently.

`build.toml`, `package/*/build.toml`, and `build-support/static-core/build.toml`
declare incremental rules. Static-linking overrides live in
`build-support/static-core/static-core.mk`; the Linux entry-marker hook lives
beside its board patch in `board/rpi4/kernel-entry.mk`. Both are explicitly
included by `external.mk`. A component is
skipped only when its input contents, permissions, selected configuration,
dependencies, rule and outputs match. Final assembly and verification always
run. Each output is locked during workflow operations; failures invalidate
incremental state. `build` stops after image assembly; `image` snapshots the
result and includes capture-based EEPROM preparation only when `--capture` is supplied.

Rule paths are repository-relative unless absolute. Placeholders are `{repo}`,
`{buildroot}`, `{profile}` and `{output}`. `config` selects symbols with globs,
`config_paths` tracks files named by config values, and `depends_on` orders rules
and propagates changes. Add a package manifest when adding a component that
needs explicit incremental refresh logic.

Python packages separate responsibilities: `src/profiles` composes and executes
profiles, `src/build` tracks incremental work, `src/artifacts` handles images and
snapshots, `src/diagnostics` runs smoke tests, and `src/sbc_controller` handles host links.
`src/setup` installs downloaded tools and the optional host permissions policy.

## Storage and static linking

Images contain a 128 MiB boot FAT, a 120 MiB EROFS root partition, and a 64 MiB
ext4 partition labelled `polis-data`. The speed profile packages boot files into
a compact FAT16 ramdisk inside the boot FAT. Its size is derived from the payload.

`/` is read-only. `/tmp`, `/run` and `/dev/shm` are volatile. `/data` is mounted
before normal services; `/data/var-lib` supplies persistent `/var/lib`. Package
state is seeded once, with retry after interruption. Missing/damaged storage
blocks services and is never silently formatted. Final images grow `/data` on
first boot; `--expand-data` during flashing is an optional alternative.

The static policy requires BusyBox and the selected core executables to have no
ELF interpreter or dynamic dependencies. It covers selected iwd, D-Bus, kmod, XZ,
GPIO-test and firmware diagnostic programs. Shared libraries remain available
for other applications; the policy does not force every package to support
fully static linking.

## Product services and diagnostics

The speed profile enables iwd, Broadcom Wi-Fi firmware, DHCP and openresolv DNS.
Runtime Wi-Fi provisioning remains pending integration of our `wifi-connect`
fork. Images contain no build-time Wi-Fi credentials. The `iwctl` client is
omitted; provisioning will use D-Bus. Persistent iwd state lives under `/var/lib`.

UART0 on GPIO14/TX and GPIO15/RX (header pins 8/10) carries 115200-baud kernel
logs. Use 3.3 V levels and common ground. Bluetooth routing frees UART0.
The speed profile has no SSH or interactive getty. USB-C provides the built-in
CDC ACM diagnostic gadget, identified as `Polis_Pi_Diagnostics`.

The optional `boot-diagnostics` group launches a background collector after
initial mounts. It waits for writable `/data`, saves one immutable capture, and
exits. It uses no GPIO pins or UART device. Captures live at
`/data/boot-diagnostics/<boot-id>/` and contain kernel logs, mount/command-line
context, and firmware/EEPROM queries. USB diagnostics transfers those captures
to the host. Cut-down firmware may lack retained VideoCore messages; failed
queries get error files without discarding other logs.

The collector and its `boot-diagnostics-tools` helper live in
[`package/polis-boot-diagnostics/`](package/polis-boot-diagnostics/README.md).
The former GPIO/LED probe is removed. Old captures remain readable, but new
captures do not provide GPIO/UART-open readiness measurements.

## SBC controller test fixture

[`package/polis-sbc-controller`](package/polis-sbc-controller/README.md) supplies
Pico 2 W firmware, a host daemon and CLI/MCP frontend for power, UART, verified
SD reflashing and recovery. It is separate from the Pi image.

With the Pico in BOOTSEL mode, `make sbc-controller-flash` builds, flashes, verifies and
starts its firmware. Resetting the Pico switches Pi power off. `PICO_SERIAL`
selects a particular Pico. `make sbc-controller-daemon` runs the daemon in the foreground.

```sh
make sbc-controller-cli ARGS=status
make sbc-controller-usb-test
make sbc-controller-diagnostics
make sbc-controller-benchmark ARGS="--samples 5"
```

The benchmark cycles Pi power. Fixture configuration is in
[`daemon/src/settings.rs`](package/polis-sbc-controller/daemon/src/settings.rs).
See [host setup](package/polis-sbc-controller/docs/host.md),
[recovery prerequisites](package/polis-sbc-controller/docs/recovery.md) and
[measurement definitions](package/polis-sbc-controller/docs/benchmark.md).

## Snapshots, flashing and smoke tests

Snapshots are named, read-only captures containing images, effective configs,
profile/audit files, selected EEPROM artifacts, workspace sources and checksums.
Existing names are never overwritten. Failed captures retain `.incomplete` and
are excluded from selection. Historical snapshots remain usable as evidence.

```sh
make images
make devices
make flash ARGS="--image snapshots/my-speed-image/images/sdcard.img --device /dev/sdX"
make image ARGS="--profile polis-rpi4-speed --development --name my-speed-dev"
make smoke ARGS="--snapshot my-speed-dev --target init --runs 3"
```

Flashing requires explicit device confirmation. Check the disk identity before
confirming; its contents will be replaced. `--expand-data` grows persistent
storage after flashing. The flash command does not prepare or apply EEPROM.

Smoke tests boot a disposable copy in QEMU, leaving snapshots unchanged. The
runner requires the `raspi4b` machine and `fdtput`; use `--qemu`/`--fdtput` to
supply tool paths. It needs approximately 2 GiB guest RAM plus emulator overhead.
The speed profile's runtime hooks provide `init`, `storage` and `services`
readiness markers. The base has no diagnostic marker; use a suitable `--expect`
for a base smoke test. Emulation does not establish physical boot time, GPIO
success, or hardware Wi-Fi availability.

## Verification and benchmark evidence

```sh
make test
python -m src.profiles.schema --check
python3 -B board/rpi4/post-firmware/test_steps.py
python3 tests/filesystems.py --buildroot /path/to/buildroot --host-dir /path/to/output/host
```

The filesystem integration check resolves both profiles and exercises real
`genimage` with nonbootable fixtures. It does not compile a kernel or write to
block devices. Other host tests use temporary files, simulated hardware and
mocked build commands.

Historical measurements remain in [benchmarks](benchmarks/) and the
[pre-Linux runlog](package/polis-sbc-controller/docs/prelinux-runlog.md).
Experiment identifiers belong to that evidence and manifest `evidence` fields.
They are not build modes. Changing a profile does not establish a new measured
boot time; measure the resulting SD image and record its EEPROM configuration.
