# Polis Buildroot images

`BR2_EXTERNAL` tree for Buildroot **2026.08**, targeting a Raspberry Pi 4 Model B
with a microSD card. `polis_rpi4` preserves the first glibc-based configuration;
`polis_rpi4_musl` uses musl and static core executables. No board is selected by default.
Both derive from that release's `raspberrypi4_64_defconfig`. Three additional
musl profiles compare EROFS, SquashFS, and initramfs against the existing ext4 image.
`rpi4_stock` provides the upstream 64-bit Pi 4 configuration with a common
boot-timing probe as the stock Buildroot baseline.

## Stock Raspberry Pi 4

`configs/rpi4_stock_defconfig` follows Buildroot **2026.08**'s
`configs/raspberrypi4_64_defconfig`, adding only the common post-build timing
probe installer. It uses the upstream glibc toolchain,
`bcm2711` kernel configuration, firmware settings, writable ext4 root,
Ethernet DHCP, and serial/HDMI consoles. This is a minimal Buildroot image;
Raspberry Pi OS and its desktop are not included.

```sh
make configure ARGS="--board rpi4_stock"
make build ARGS="--board rpi4_stock"
```

The image is written to `output/rpi4_stock/images/sdcard.img`, and `build`
saves a snapshot through the usual workflow. The board also appears in
`make boards` and the interactive build menu.

This profile has no Polis overlays, hardware-trimming fragments, static-linking
policy, or iwd setup. `make smoke` waits for the common `POLIS_SMOKE_READY init`
marker after all init scripts return. Its 15-second wait for the absent Ethernet
interface in QEMU is intentionally retained for diagnostic comparison.
The hardware, Wi-Fi, and tuning descriptions below
describe the `polis_*` profiles.

## Hardware and baseline

- AArch64, the upstream pinned Raspberry Pi kernel and Bootlin toolchain.
- BusyBox init and the upstream genimage tooling. Musl profiles compare four
  read-only roots with a shared writable data partition; glibc retains ext4.
- Onboard Wi-Fi with Broadcom firmware and iwd for association and DHCP,
  with openresolv for DNS. NetworkManager and wpa_supplicant are disabled.
  Runtime network provisioning is pending integration of our `wifi-connect` fork.
- UART0 (`/dev/ttyAMA0`) reserved for the test on GPIO14/TX and GPIO15/RX
  (header pins 8 and 10). Use 3.3 V UART levels and a common ground.
- No SSH, serial getty, HDMI getty, or kernel serial console. Bluetooth is
  disabled to free UART0, following the Raspberry Pi
  [UART documentation](https://www.raspberrypi.com/documentation/computers/configuration.html#configuring-uarts).
- A kernel fragment disables Ethernet, USB, Bluetooth, graphics/framebuffer,
  virtual terminals, audio, and media support. A BusyBox fragment removes cron.

The test application is not included yet. There is no interactive management
connection in this profile. Images never require Wi-Fi credentials at build
time; provisioning will happen on the device through our forthcoming fork of
`balena-os/wifi-connect`.

## Wi-Fi provisioning (pending)

When the fork is available, add its Buildroot package under `package/` and make
it a dependency of our Wi-Fi recipes. The fork must use iwd's D-Bus API:
[upstream wifi-connect](https://github.com/balena-os/wifi-connect) requires
NetworkManager and cannot be used unchanged with this image. Integrate its
startup and provisioning flow after inspecting the fork.

The current image starts iwd and D-Bus, but does not start a provisioning portal
or include any saved networks. Only loopback is configured by ifupdown; iwd
owns wireless association and IP configuration. Its built-in DHCP client is
enabled in `rootfs-overlay/etc/iwd/main.conf`, and openresolv updates DNS after
connection. The musl profiles persist runtime network profiles under `/data/var-lib/iwd`,
bind-mounted at `/var/lib/iwd` through the shared `/var/lib` mount. The original
glibc profile keeps its writable ext4 root. There is no credential-copy hook or
build-time credential file. The `iwctl` client is omitted to avoid adding readline;
the provisioning application will control iwd through D-Bus.

## Linking policy

The musl profile uses `BR2_SHARED_STATIC_LIBS=y` to retain dynamic-linking
support for future BlueZ use. That option alone still links applications
dynamically. `BR2_POLIS_PREFER_STATIC` adds package-specific settings:

| Component | Requested linking |
| --- | --- |
| BusyBox (init, shell, utilities) | Static via `busybox-static.fragment` |
| iwd | Fully static with libtool `-all-static` and static dependency metadata |
| D-Bus daemon and utilities | Static, with libdbus.a available to clients |
| Kmod tools | Static, with compression libraries linked directly instead of dlopen |
| XZ utilities | Static; liblzma also remains available as a shared library |
| BlueZ (future) | Dynamic linking allowed; not enabled yet |

The policy lives in `package/polis-linking/polis-linking.mk`. Kmod has a small
optional Meson patch under `patches/musl/kmod/`; its default remains off for host
tools. No global `-static` is imposed on shared-library or host-tool builds.
New application recipes should explicitly request static executable linking;
document exceptions for applications requiring shared libraries or plugins.

`board/polis/rpi4/check-static.py` runs after target installation and rejects
core executables with an ELF interpreter or shared-library dependencies. This
checks the built result instead of silently accepting a dynamic fallback.
Configuration checks do not prove successful linking: the first musl build and
hardware boot test are still required. Boot latency is the acceptance metric.

BlueZ is kept compatible for later, with Bluetooth still disabled and UART0
reserved for the test. Enabling onboard Bluetooth will require revisiting that
hardware configuration as well as adding the package.

## Commands (Make + uv)

Use the uv environment configured by `pyproject.toml` and `.python-version`:

```sh
make setup
make
make boards
```

`make clean` removes the repository's `.cache/` directory, including cached
tools and smoke-test results. `make purge` removes all build artifacts under
`output/` and recreates an empty output directory. Both leave `snapshots/` intact
and run without Python or uv.

For command-specific help, use `make smoke help` (or `make build help`, etc.).
`make smoke ARGS="--help"` and `make smoke -- --help` also work. Help requests
display the command's options without running its action. Bare `-h`/`--help`
flags, as in `make smoke --help`, are handled by Make and show Make's own help.

Pass flags to any CLI command after `--`, for example:

```sh
make smoke -- --runs 3
make smoke -- --snapshot smoke-baseline --target services --runs 3
make build -- --board polis_rpi4_musl
```

The separator is required so Make does not interpret the flags itself. The
existing `ARGS="..."` form also works; use it for values containing spaces or
`=` (Make treats `NAME=value` as a variable assignment), for example
`make smoke ARGS="--expect 'POLIS_SMOKE_READY storage' --runs 3"`.

The CLI uses Python's standard library, so no third-party dependencies are
needed. The Makefile is a thin wrapper around the Python CLI in `src/`;
board discovery, selection, build commands, snapshots, and flashing live in Python.
Buildroot itself still uses GNU Make; the CLI invokes it with an argument list,
without a shell. Target init scripts
remain BusyBox shell scripts and do not require Python on the Pi.

There is no default board. Running `make` displays commands and all
available boards. Running a build action without `--board` lists boards and prompts
for a selection in a terminal. Without a terminal, it only lists options and
performs no action. Blank selections cancel; they never choose the first option.

```sh
make build
# Or select the board without a prompt:
make build ARGS="--board polis_rpi4_musl"
```

`build` selects the board once, reloads its saved defconfig, builds the image,
and saves a read-only snapshot under `snapshots/<board>-YYYYMMDD/`, using the
America/Chicago calendar day. Additional snapshots for that board and day use
`-2`, `-3`, etc., without overwriting existing captures.
Fresh output directories are configured automatically. Each step must succeed
before the next starts; failed builds do not create snapshots. Use `--name`
to give the snapshot a custom name, for example
`make build ARGS="--board polis_rpi4_musl --name musl-baseline-v1"`.
Invalid or existing names are rejected before the build starts.

To retain unsaved menuconfig changes, use `make build
ARGS="--board polis_rpi4_musl --no-configure"`; this builds the existing
configuration and still saves a snapshot. The separate `configure` and
`snapshot` commands remain available for individual steps.

Boards are discovered from `configs/*_defconfig`. Every board uses
`output/<board>/`. Use `--output PATH` for an explicit custom build directory.
Keep build directories at their original paths: generated host tools embed
absolute paths, so moving a build directory requires a fresh build.

The default build flow reloads the saved defconfig.
Save menuconfig changes with `savedefconfig` before using the default flow.

If an existing musl build reports `usr/bin/xz still needs dynamic linking`,
clean and rebuild XZ after updating the linking policy. Buildroot's package
stamps do not track changes to linker options:

```sh
make -C output/polis_rpi4_musl xz-dirclean
make build ARGS="--board polis_rpi4_musl"
```

Use the matching output directory and board name for other musl profiles.

Pass CLI options through `ARGS`, for example:

```sh
make build ARGS="--board polis_rpi4_musl --downloads /path/to/dl"
```

Available path options:

- `--buildroot PATH`: defaults to `/home/broosegoose/Software/buildroot-2026.08`.
- `--downloads PATH`: defaults to that Buildroot tree's `dl/` cache.
- `--output PATH`: explicitly overrides the selected board's output directory.
- `--snapshots PATH`: overrides this repo's `snapshots/` directory.

Keep the same board/output options for configure, build, and customization.
Changing libc/toolchains needs a fresh output directory. Rebuilds in the same
output reuse completed work; different outputs share downloads but compile
independently. No commands implicitly build or flash as part of listing options.
The resulting image is `output/<board>/images/sdcard.img`.

```sh
make test
```

These host-tool tests use temporary files and mocked build/flash commands.
They do not build images or write block devices.

## Local QEMU smoke tests

`make smoke` lists completed snapshots for selection, boots a disposable copy
of the selected snapshot in QEMU, and stops when it
reaches a serial readiness target. This is a rough local regression check for
kernel/userspace startup, filesystem mounting, and service readiness. Docker
alone shares the host kernel and would not exercise these boot paths.

On Debian/Ubuntu, install the host tools:

```sh
sudo apt-get install qemu-system-arm device-tree-compiler
```

The runner requires QEMU's `raspi4b` machine (QEMU 9 or newer). It checks for
that machine before starting. `--qemu` and `--fdtput` accept executable paths;
it also discovers locally extracted tools under `.cache/qemu/root/usr/bin/`.
No root privileges, mounts, network access, or Docker daemon are needed to run
the test. Allow about 2 GiB of guest RAM plus emulator overhead and space for
one disposable SD-image copy.

Build once to include the opt-in probe, then run repeated tests:

```sh
make build ARGS="--board polis_rpi4_musl --name smoke-baseline"
make smoke ARGS="--snapshot smoke-baseline --target init --runs 3 --results .cache/smoke/baseline"

# Or choose the snapshot, target, and number of runs interactively:
make smoke
```

Smoke tests use `snapshots/` exclusively; `--snapshots PATH` selects another
snapshot collection. Incomplete captures are excluded. Without a terminal,
omitting `--snapshot` only lists available captures and performs no boot;
omitting both `--target` and `--expect` lists targets and performs no boot.
There is no implicit newest/first choice. To test an existing working build,
capture it with `make snapshot ARGS="--board polis_rpi4_musl --name my-test"`,
then use `make smoke ARGS="--snapshot my-test"`.

The target menu offers `init`, `storage`, and `services`; `--target NAME` skips
that prompt. The run-count prompt defaults to **3** when left blank; `--runs N`
skips it. Without a terminal, the run count defaults to 3.

The `init` target waits for `POLIS_SMOKE_READY init`. A common
post-build hook wraps BusyBox's `rcS` startup command while preserving the musl
data-mount guard. It emits the marker only after all init scripts return,
before getty starts.
The probe writes directly to `/dev/ttyAMA0`, so it also works with `console=null`
and getty disabled. It runs only with `polis.smoke=1`, which the runner adds;
normal hardware boots leave the application UART untouched.

This endpoint includes each profile's service startup scripts and stock's
Ethernet wait. It measures completion of the init sequence, not successful
network configuration or daemon readiness. Stock's expected Ethernet failure
therefore does not prevent this marker. The deeper storage/service probes are
skipped for `init` timings. The CSV records the selected target separately.

Rebuild and capture new snapshots to include the common probe. Existing
snapshots remain unchanged; they can still use their original explicit
`--expect` target. The smoke command itself never builds or modifies a source
image or snapshot.

For the deeper Polis checks, use:

```sh
make smoke -- --snapshot smoke-baseline --target services --runs 3
```

Selecting `services` or `storage` adds `polis.smoke.checks=1`, enabling the
`S99polis-smoke` checks in the Polis images. Stock has no D-Bus/iwd probe.

The Polis services target requires:

- For the musl profiles: read-only `/`, writable tmpfs `/run` and `/tmp`, writable
  ext4 `/data`, a working `/var/lib` bind mount, and successful state-file writes
  and reads. The original glibc profile instead checks its writable ext4 root.
- A successful D-Bus call to iwd's ObjectManager after its startup script ran.
  Merely starting a daemon process is insufficient.

Use `--target storage` to stop after the storage checks.
That marker is emitted by the late init script, **after earlier service startup
scripts have run**; it does not measure an early storage-only boot milestone.
For an application-specific target, have the application emit a unique line to
`/dev/ttyAMA0` in smoke mode and pass that text with `--expect`. Matching is
literal and requires a complete newline-terminated line, except for targets
ending in `login:`. Custom targets only
establish what the emitting application actually checks.

Every run uses a fresh writable copy of the source SD image, padded to the
power-of-two capacity required by QEMU. Guest changes are discarded at the end;
source credentials/state are copied as-is. This tests first-boot initialization
when the source is pristine, but does **not** test persistence across reboot.
Use equivalent source state when comparing variants.

**Read `benchmarks/software-boot-times.csv` for benchmark results.** Every smoke
session automatically updates one row with its source, target, passed/attempted
count, median/min/max seconds, and log directory. Times measure host elapsed
time to the target, excluding preparation/copying. Statistics include successful
runs only; all-failed sessions have blank timing cells, so timeouts cannot look
like fast boots. `benchmarks/boot-times.csv` is reserved for manually recorded
physical Pi measurements and stays empty until hardware timings are taken.

Detailed evidence goes into a new `.cache/smoke/<timestamp>/` directory, or the
explicit `--results` directory, which must not already exist. `run-001.log`, etc.
contain serial output. `results.json` retains hashes, QEMU version, command lines,
individual timings, and guest uptime for debugging; it is not the primary
benchmark view. Failures, early QEMU exits, and timeouts return nonzero; the
default timeout is 120 seconds per run (`--timeout 240` overrides it). Ctrl-C
stops QEMU, records the interrupted run, and removes its disposable disk.

For a rough before/after comparison, build a second named snapshot and run the
same command with a new results directory. Compare rows in the benchmark CSV,
using the medians and min/max spread with the same target, run count, QEMU version, host load, and
source data state. A difference smaller than the run-to-run spread is weak
evidence. These timings include CPU emulation and host scheduling/cache effects;
they cannot predict a Pi's power-on latency or SD-card performance. Keep them
separate from the hardware measurements in `benchmarks/boot-times.csv`.

The runner loads `Image`, the device tree, and (for the initramfs profile)
`rootfs.cpio.lz4` directly, using the packaged `rpi-firmware/cmdline.txt`. It
bypasses EEPROM/VideoCore boot firmware and the FAT boot partition. A temporary
device tree assigns QEMU's older SD controller to `mmc0`, disables the real
Pi 4 SD controller, and frees PL011 as `ttyAMA0` for the probe. The production
device tree is untouched. This adaptation intentionally means that passing
does not validate the real Pi SD controller or firmware overlays.

[QEMU's Pi model](https://www.qemu.org/docs/master/system/arm/raspi.html) has
hardware gaps. The probe verifies iwd's software interface, not Wi-Fi
association, DHCP/DNS, RF behavior, provisioning, or physical UART timing.
Other init scripts can also fail without failing rcS; only the explicit probe
assertions and selected target are acceptance criteria. With `console=null`,
early kernel/storage failures may produce only a timeout. A diagnostic build
with a serial console can provide more detail, but changes the timing workload.

The ext4 musl profile has been boot-tested with QEMU 10.2.1. The runner also
selects the existing SquashFS, EROFS, and LZ4 initramfs artifacts through their
snapshot paths; those profiles still need completed builds and
their own boot verification. Real Pi testing remains necessary for acceptance.

## Filesystem comparison (musl)

The existing `polis_rpi4_musl` profile is the ext4 baseline. Three additional
profiles select the other filesystems through the same `--board` workflow. Each
produces a flashable `output/<board>/images/sdcard.img`:

| BOARD | Root image | Root storage |
| --- | --- | --- |
| `polis_rpi4_musl` | `rootfs.ext4`, 120 MiB | Partition 2, mounted read-only |
| `polis_rpi4_musl_erofs` | `rootfs.erofs`, LZ4HC level 9 | Partition 2, read-only |
| `polis_rpi4_musl_squashfs` | `rootfs.squashfs`, LZ4 | Partition 2, read-only |
| `polis_rpi4_musl_initramfs` | `rootfs.cpio.lz4`, complete root | RAM-backed, remounted read-only before services |

For example:

```sh
make build ARGS="--board polis_rpi4_musl_erofs --name musl-erofs-lz4hc-v1"
```

Select each desired variant explicitly from the table or the CLI board menu.

Start each variant in a fresh output directory, including ext4 when moving from
the former writable-root layout. All four retain the musl toolchain, static
executable policy, pinned kernel and firmware, hardware configuration, packages,
and services. Filesystem choices live in the defconfigs;
`board/polis/rpi4/filesystems/` supplies the kernel fragments, command lines,
shared runtime overlay, and declarative genimage layouts. All four use Buildroot's
`support/scripts/genimage.sh`; the wrapper discovers the defconfigs automatically.
The glibc profile is unchanged.

The MBR partition layout is identical across all four musl profiles:

| Partition | Size | Purpose |
| --- | --- | --- |
| 1 | 128 MiB, FAT | Firmware, device tree, overlays, kernel; also the LZ4 archive for initramfs |
| 2 | 120 MiB | Root image; reserved and unused for initramfs |
| 3 | 64 MiB, ext4, label `polis-data` | Persistent `/data`, read-write with `noatime` |

Reserving partition 2 for initramfs keeps `/data` at `/dev/mmcblk0p3` with the
same offset in every image. The FAT size accommodates both the kernel and the
initramfs. Compressed roots are padded by the partition layout, not inflated
inside their filesystem images. Compare the root artifact sizes as well as the
SD image size. Genimage creates `data.ext4` as a fresh empty filesystem; flashing
a complete `sdcard.img` **replaces existing data and credentials**. Back up `/data`
before reflashing if you need to retain them.

All musl roots operate read-only. `/run` and `/tmp` are tmpfs, while the standard
Buildroot transient `/var` paths point into them and `/etc/resolv.conf` points
into `/run`. Before starting any services, `mount-data` waits for the data
partition (needed when initramfs outruns SD probing), mounts `/data`, initializes
`/data/var-lib` from the packaged `/var/lib` on first boot, and bind-mounts it at
`/var/lib`. This preserves iwd profiles, the D-Bus machine ID, and the random seed.
Application configuration belongs in `/data/config`, also exposed as
`/etc/polis`; additional application state can use `/data` or `/var/lib`.
No overlayfs is used. A missing or unmountable data partition prevents service
startup; boot never formats a device or silently falls back to volatile state.
Services stop before BusyBox unmounts the bind mount and data partition.

The initramfs root remains in RAM throughout operation. Buildroot's full CPIO
`/init` mounts devtmpfs before BusyBox init; the shared startup script explicitly
remounts `/` read-only because the kernel command-line `ro` flag alone does not
protect an unpacked initramfs. Its data partition is mounted just like the other
variants. The firmware loads the archive using
[`initramfs rootfs.cpio.lz4 followkernel`](https://www.raspberrypi.com/documentation/computers/config_txt.html#initramfs),
and the kernel has LZ4 initrd decompression enabled. The archive is external,
not embedded in `Image`.

EROFS and SquashFS drivers and their LZ4 decompressors are built into the kernel;
they cannot depend on modules inside the unmounted root. Ext4 and tmpfs support
are built in for every variant. SquashFS uses 128 KiB blocks and ordinary LZ4:
Buildroot's default `-Xhc` option is explicitly disabled. Other kernel settings
remain shared through the existing `linux.fragment`.

Snapshot each completed image and record repeated cold-boot timings as described
below. Separate first boots (state initialization) from subsequent boots, and
use equivalent provisioned state for all four. Configuration and image-layout
checks do not replace a hardware boot test: verify the same application readiness
milestone, read-only `/`, tmpfs `/run` and `/tmp`, persistent `/data`, iwd/D-Bus/DNS,
and UART operation. Confirm a file in `/data/config` and an iwd profile survive
reboot. No boot-time improvement is claimed before measurements.

Run the configuration and image-packaging regression check with an existing
Buildroot host directory containing genimage, e2fsprogs, dosfstools, and mtools:

```sh
uv run python tests/filesystems.py \
    --buildroot /home/broosegoose/Software/buildroot-2026.08 \
    --host-dir output/polis_rpi4/host
```

The test resolves all four defconfigs, checks that unrelated Buildroot settings
match, and runs the real image generator against small non-bootable fixtures.
It checks FAT contents, root placement, the ext4 data volume, and identical MBR
layouts without mounting filesystems or writing a block device.

## Preserve comparison builds

Every successful `make build` saves a snapshot automatically. Build output
directories remain working directories; rebuilding regenerates their images.
To capture an existing completed, idle build separately:

```sh
make snapshot ARGS="--board polis_rpi4_musl"
make snapshot ARGS="--board polis_rpi4_musl --name polis-rpi4-musl-v1"
```

Both commands use the same snapshot layout and default naming:
`<board>-YYYYMMDD`, then `-2`, `-3`, etc. `--name` supplies a custom name.
The selected output must contain its own `.config` matching the board;
`--output PATH` overrides its location. There is no parent-directory config lookup.

Snapshots live under `snapshots/<name>/` (ignored by Git). The command refuses
to reuse a name and makes the completed snapshot read-only. It copies all image
files, the resolved Buildroot/kernel/BusyBox configs when available, the saved
defconfig, and the external workspace at capture time. It records SHA-256
checksums, capture/source timestamps, and sizes in `sizes.tsv`, including ext4
used space when `dumpe2fs` is installed. Files are independently copied, never
hard-linked to build outputs. `external-at-capture.tar.gz` records the workspace
as found; it does not prove which source revision produced a past build.

Each snapshot contains `images/`, `configs/`, `metadata.txt`, `sizes.tsv`,
`SHA256SUMS`, and `external-at-capture.tar.gz`. Root filesystem artifacts differ
by board profile (ext4, EROFS, SquashFS, or initramfs); those are image formats,
not different snapshot layouts. Buildroot's ext4 output also has a `rootfs.ext2`
filename with a `rootfs.ext4` alias; both are copied as independent files.

An interrupted or failed snapshot has an `.incomplete` marker and is excluded
from image selection. Completed snapshots appear in `make images` and
`make flash`, or can be opened in Raspberry Pi Imager. Check integrity with:

```sh
cd snapshots/polis-rpi4-v1
sha256sum -c SHA256SUMS
```

Use separate defconfigs and output directories for optimization experiments.
Record boot measurements in `benchmarks/boot-times.csv`, referencing the snapshot
name. Leave unavailable timings empty; none have been measured yet. Use the same
Pi, EEPROM settings, power supply, SD card, and access point, with repeated cold
boots. Measure power-on to UART application readiness separately from Wi-Fi
connectivity and provisioning-portal readiness. The stock image does not run our
test application, so a login prompt is not an equivalent readiness milestone.

## Optimization experiments

[instant-pi](https://github.com/AndreVallestero/instant-pi) suggests musl,
static linking, LTO, a smaller kernel, cut-down firmware, and filesystem changes.
Its published timings are for Pi Zero W/1B and a shell-ready workload, so they
are not predictions for this Pi 4 Wi-Fi appliance.

Suggested sequence, each in a separate variant:

The objective is minimum power-on-to-test-ready time, not minimum binary size.
Size measurements help explain loading costs; they are not the acceptance
criterion. Prioritize services, initialization steps, and waits that delay the
test. Measure Wi-Fi readiness separately, and only put it on the test's startup
path if the test actually requires a connection.

1. Measure the saved stock and current custom builds, then trim more unused
   kernel features and startup work based on those measurements. Start the UART
   test as soon as its prerequisites are ready; avoid gating it on provisioning
   or network association unless needed. Audit driver probes and init scripts
   before treating compiler flags as the main optimization.
2. Try the Pi 4 cut-down firmware variant (`start4cd.elf`/`fixup4cd.dat`), with
   matching firmware settings verified on hardware. Do not copy the older Pi
   firmware filenames or 32-bit kernel-loading settings directly.
3. Compare the glibc baseline with the new musl profile, which requests static
   core executables while retaining dynamic support for BlueZ.
   Static linking may reduce executable startup overhead; judge it by measured
   boot latency even if binaries grow. It does not eliminate the work performed
   by iwd, D-Bus, or the application during startup. Recheck iwd/D-Bus and the future portal's
   dependencies under the new toolchain.
4. Evaluate LTO only for packages that support it. Buildroot's global LTO option
   applies to participating packages, not every package or the kernel.
5. Compare the musl filesystem variants above, with identical persistent data
   layouts and equivalent iwd provisioning state. Smaller partitions reduce image/flash size but do not by
   themselves demonstrate faster boot. Keep filesystem and toolchain changes
   separate so their effects can be compared.

The musl variant implements the linking experiment, and the three filesystem
variants provide the filesystem experiment. The remaining changes are future
experiments. Config validation cannot establish a boot-time improvement.

## Flash an SD card

```sh
make images
make devices
make flash
```

Image discovery searches `output/<board>/` for boards in `configs/`,
completed snapshots (using the same checks as `smoke`), and an optional explicit
`--output` directory. It does not scan Buildroot's output or unrecognized output
directories. Empty images and incomplete captures are excluded. Duplicate paths are
listed once. `flash` lists images and whole disks for explicit selection, then
requires typing the destination device path to confirm erasing it. It rejects
partitions, read-only disks, mounted descendants/active swap, and undersized
media. It rechecks the destination and source after confirmation and any sudo
prompt. Nothing is selected by default; flashing requires a terminal.

```sh
make flash ARGS="--image /path/to/sdcard.img --device /dev/sdX"
```

To use the rest of the card for persistent application data:

```sh
make flash -- --expand-data
# Or select both paths explicitly:
make flash ARGS="--image /path/to/sdcard.img --device /dev/sdX --expand-data"
```

`--expand-data` supports the four musl profiles' three-partition MBR layout,
with a final ext4 partition labeled `polis-data`. Before writing, it validates
the source layout and filesystem, required host tools, and destination sector
size. After writing the small image, it grows partition 3 to fill the card,
checks and resizes its existing filesystem, verifies its size, and flushes the
device. Boot and root partitions retain their sizes; the source image is never
modified. Existing contents packaged in `/data` are preserved by expansion,
but flashing still erases the previous installation on the card.

Expansion requires Linux `sfdisk`, `blkid`, `blockdev` (util-linux), `udevadm`
(udev), and `e2fsck`, `resize2fs`, `dumpe2fs` (e2fsprogs). It supports 512-byte
logical sectors and cards up to the MBR limit of 2 TiB. Leave the flag off for
fixed-layout benchmarks and the stock/glibc images, which have no separate data
partition. Expansion adds host-side metadata work, not a larger image copy or
first-boot resize. If expansion fails after flashing, the command reports that
the card was already written; resolve the reported error and reflash before use.

Explicit paths still require confirmation. Replace `/dev/sdX` with the SD card's
whole-disk device and unmount its partitions first. The Python helper calls
Linux `lsblk` for device metadata and `dd` with `conv=fsync` for the confirmed
write; it never mounts, unmounts, or starts a build. Raspberry Pi Imager's
**Use Custom** option is also supported for any saved `sdcard.img`.

## Customize

```sh
make menuconfig ARGS="--board polis_rpi4_musl"
make savedefconfig ARGS="--board polis_rpi4_musl"
```

`configure` and the default `build` flow reload the saved defconfig and replace
unsaved menuconfig changes. Use `build --no-configure` through `ARGS` to retain
those changes for one build, or save them first.
`make savedefconfig ARGS="--board polis_rpi4_musl"` writes back to `configs/<board>_defconfig`. Commands
that use an existing configuration check that it matches the selected board.

- `board/polis/rpi4/config.txt`: Raspberry Pi firmware and UART configuration.
- `board/polis/rpi4/cmdline.txt`: kernel command line.
- `board/polis/rpi4/linux.fragment`: kernel additions and removals.
- `board/polis/rpi4/busybox.fragment`: BusyBox changes.
- `board/polis/rpi4/rootfs-overlay/`: files copied into the target filesystem.
- `board/polis/rpi4/rootfs-overlay/etc/iwd/main.conf`: iwd network and DNS settings.
- `package/`: add test software recipes here, with a `source` entry in
  `package/Config.in`. `external.mk` includes `package/*/*.mk` automatically.

After changing the kernel or BusyBox fragments in an existing build, use the
corresponding Make target to reconfigure that component:

```sh
make linux-reconfigure ARGS="--board polis_rpi4_musl"
make busybox-reconfigure ARGS="--board polis_rpi4_musl"
make build ARGS="--board polis_rpi4_musl"
```

Use a fresh output directory after removing packages to avoid stale files in
the target filesystem; for example
`make build ARGS="--board polis_rpi4_musl --output output/musl-clean"`.

For the switch from wpa_supplicant to iwd, use a fresh output directory if the
old configuration has already been built. Disabling a package does not remove
its previously installed binaries and configuration from an existing target tree.

## Boot-time tuning

The baseline removes unused hardware support and services, but boot-time gains
need measurement on the device. Wi-Fi loads through `S11modules` after its
firmware is available on the root filesystem. Buildroot starts D-Bus with
`S30dbus-daemon`, followed by `S40iwd`. iwd handles association and DHCP once a
network is provisioned; the portal startup will be defined with the `wifi-connect`
integration. Applications that need networking should wait for connectivity
themselves. Buildroot's iwd package also enables its required kernel crypto options.

Keep kernel logging available in memory for diagnosis. Measure power-on to
test readiness (and separately Wi-Fi readiness) before trimming more drivers
or changing firmware settings. Kernel config validation alone does not verify
hardware boot, Wi-Fi association, or UART traffic.
