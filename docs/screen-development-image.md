# Pi 4 HDMI capture and display development

Build `polis-rpi4-screen-dev`. It composes **all of `polis-rpi4-dev`'s feature
groups**, replacing `headless` with `rpi4-video`, adding `rpi4-uart3`,
`ffmpeg-libraries`, `rpi4-usb-host` and `polis-splash-screen`; profiles do not inherit TOML
files. Other profiles are unchanged. SSH keys, `polis-dev.local`, Ethernet,
Wi-Fi setup, persistent storage and `/data/bin/dev-app` behave as described in
[the development guide](development-image.md).
The new group reuses the existing no-getty inittab overlay without selecting
the headless kernel/firmware restrictions, so HDMI does not start a login prompt.
The display drivers remain simpledrm and VC4. `polis-splash-screen` is a
built-in Rust DRM client that draws the supplied 512×512 Polis icon on black
when VC4 initializes. The profile disables legacy fbdev, fbcon, virtual terminals
and the old kernel-logo path. There is no `/dev/fb0`, HDMI console, cursor or login
prompt. Kernel messages remain on `console=ttyAMA0,115200`.

The logo remains in kernel memory after boot so a monitor connected later can
receive it. Each connected output gets a centered logo, scaled down if necessary.
The splash skips modesetting while an application owns DRM master and reprobes
outputs when DRM calls its lastclose restoration callback. Applications need no
splash-specific launch wrapper. Restoration follows DRM lastclose, so another
open userspace DRM file on the same VC4 device can delay it; merely dropping
master does not trigger it.
The first logo appears at VC4 initialization, not at the earlier firmware or
simpledrm stage. The firmware rainbow splash stays disabled.

The existing host PNG-to-PPM preparation remains unchanged. The new Rust host
helper validates that PPM and embeds persistent RGB pixels in the kernel; there
is no target image decoder, userspace splash service or additional target library.
Run `python3 -m src.profiles.logo --check` to verify artwork freshness. Only changed
artwork needs host ImageMagick. Rust bindings use the pinned kernel's real C
headers and a private Rust adapter; see [implementation and host prerequisites](../package/polis-splash-screen/README.md).

After building and flashing, check boot with a display, boot without a display
followed by HDMI insertion, unplug/replug, and application takeover/exit. Repeat
with both HDMI outputs. These are hardware acceptance checks, not claims made by
the host tests. DRM card numbers can change.
See the [logo source](../profiles/groups/rpi4-video/splash/README.md).

Build the image directly; no separate graphics executable needs staging:

```sh
make image ARGS="--profile polis-rpi4-screen-dev"
```

The command uses the pinned Buildroot 2026.08 tree; override its location with
`--buildroot /absolute/path/to/buildroot-2026.08`. Outputs are under
`.output/`: `images/sdcard.img`,
`profile/audit.txt`, `profile/effective-kernel.config`, and `staging/` (the
cross-compilation sysroot). A successful automatic image build also prints its
snapshot directory. First-boot EEPROM setup is enabled. No image expands
storage on boot; perform expansion during host provisioning.
Use `make configure` with the same arguments for configuration only.

Host-side expansion with `make flash -- --expand-data` can leave ext4
initializing new inode tables
in the background (`ext4lazyinit`) when the Pi mounts it. Successful expansion
and flushing do not guarantee that those tables have been initialized.
The ACT LED's `mmc0` trigger
then flashes with SD writes while SSH and Wi-Fi are already usable. Let that work
finish; its duration depends on the card. Check `/sys/class/leds/ACT/trigger`,
`/proc/diskstats`, and the `ext4lazyinit` process before treating sustained
activity as a boot failure. The separate provisioning result is in
`/run/polis-provisioning.json`.

The group enables VC4 display, V3D GLES rendering, Mesa EGL/GBM, libdrm tests
(`modetest`), V4L2 tools (`v4l2-ctl`, `media-ctl`), `kmscube`, and V3DV plus
`vulkaninfo`. Vulkan is reserved for later experiments. No X11, Wayland,
Weston, GStreamer or libcamera is selected. Buildroot's Vulkan tools recipe
builds `vulkaninfo`, **not `vkcube`**.
ALSA capture/playback uses `alsa-lib` and `alsa-utils` (`arecord` and `aplay`).
These also provide the ALSA headers, library and `alsa.pc` in the staging sysroot.

## Controller USB on the USB-A host ports

`rpi4-usb-host` replaces `usb-peripheral-only` and `no-usb` in this profile.
It enables built-in USB, VL805 PCIe/xHCI and CDC ACM, plus the existing BCM2711
PCIe, mailbox, firmware and reset-controller prerequisites. It explicitly
excludes the general-purpose defconfig's unrelated USB peripherals, including
HID, mass storage, USB network adapters and USB audio/video. Onboard GENET,
SDIO Wi-Fi, UART Bluetooth, UART3, VC4, CSI/I²C/I²S and audio remain selected.
USB-C gadget diagnostics remain disabled; `usb-diagnostics` conflicts with
this profile. Other profiles retain their selections.

The host group also selects `BR2_POLIS_RPI4_VL805_FIRMWARE`: the existing
`start4.elf` / `fixup4.dat` pair is replaced with the hash-verified upstream
VL805 decompressor fix. This adds no target package and changes no EEPROM
settings. See [the firmware hardware test](#firmware-fix-hardware-test--2026-10-22).

The controller is a USB CDC device; the passthru owns its single application
connection for inputs, HDMI facts, activation and artwork. Production no longer
uses UART3. The existing `rpi4-uart3` group remains for legacy controller tests.

### Application ownership and image build

`hdmi-graphics-hello` and its `polis-graphics-usb` supervisor are retired userspace
applications, not kernel drivers. The image no longer installs their package,
boot service, mdev event rule or staged payload. Kernel USB/xHCI/CDC ACM support
and BusyBox mdev remain enabled. Every profile runs the host-side post-build hook
`board/rpi4/install-controller-usb.py` to remove the old installed binaries, init
script, `/usr/share/polis-graphics-usb/` and their mdev rule from reused target
directories, including after a profile switch. Standard audio, input and module
rules are preserved. No replacement USB reader is installed by this removal;
`hdmi-passthru` owns the production controller connection.

Build the image through the normal workflow; no graphics payload preparation is
required:

```sh
make configure ARGS="--profile polis-rpi4-screen-dev"
make image ARGS="--profile polis-rpi4-screen-dev"
```

Outputs remain `.output/images/sdcard.img`, `.output/profile/audit.txt` and
`.output/profile/effective-kernel.config`. Image building/flashing is separate
from the host checks for this removal.

Deploy the application from `2050-hdmi-passthru` with `cargo make passthru`.
When controller options are enabled, its helper stops the retired supervisor on
older running images before opening USB. That handoff lasts until reboot;
rebuilding and flashing this image removes
the old boot service permanently. The application handles controller discovery,
coldplug and reconnect itself. See the
[controller integration guide](../../2050-hdmi-passthru/docs/screen-controller-integration.md).

### VL805 timeout evidence and prerequisites

The previous saved UART capture identified PCI device `0000:01:00.0` as VIA
`1106:3483` (VL805), with setup from 0.988 to 18.065 seconds ending in
`can't setup: -110` (`ETIMEDOUT`). A second local capture,
`.local/screen-boot-diagnostic/second-hardware-boot.log`, independently shows
PCIe link-up at 0.621 seconds, xHCI at 0.949 seconds and failure at 18.117 seconds.
Its firmware identifies itself as `2025-12-08T19:39:51`, variant `start`, hash
`2226a853bb9f5fd80392e3a4a89e457aeca88008`. No peripheral is needed to trigger
this controller setup failure. Enabling ACM cannot repair a controller that
has not initialized.

Inspection of pinned Linux `21b410140c47ffab5668399f6f143c7d7b935c8b` found:

| Prerequisite | Source / existing artifact evidence |
| --- | --- |
| PCIe and firmware drivers | Existing effective config already had `PCIE_BRCMSTB`, `RASPBERRYPI_FIRMWARE` and `RESET_RASPBERRYPI`; the new group requires them built-in, plus mailbox/reset support. |
| Device-tree reset wiring | `bcm2711-rpi-4-b.dts` and the existing compiled DTB contain `/scb/pcie@7d500000/pci@0,0/usb@0,0` with reset ID 0 referencing `raspberrypi,firmware-reset`. PCIe has no disabling status property. |
| Reset handshake | `drivers/usb/host/xhci-pci.c:xhci_pci_common_probe()` obtains and invokes the reset controller before registering the USB bus. `drivers/reset/reset-raspberrypi.c` sends `RPI_FIRMWARE_NOTIFY_XHCI_RESET` for PCI address `0x100000`, then waits 200–1000 microseconds. Its comments describe the VideoCore firmware/VL805 loading path. |
| Boot firmware selection before the fix | Full `start4.elf` / `fixup4.dat`, not cut-down firmware, from Buildroot's `raspberrypi/firmware` pin `063bcab6c8a90efb0d19f69d88cbbc7ec79cab68`. |

Those initial checks established prerequisites without isolating the cause.
The firmware experiment below subsequently resolved the timeout without
EEPROM updates, PCI reset quirks or board changes.

#### Rebuilt-image hardware test — 2026-10-22

One fresh normal power cycle through the running SBC-controller daemon
reproduced the timeout on the user's newly flashed image:

| Event | Kernel uptime |
| --- | --- |
| PCIe link up, 5.0 GT/s x1 | 0.632714 s |
| VL805 xHCI controller setup starts | 1.044681 s |
| `can't setup: -110`; controller probe fails | 18.127516 s |
| CDC ACM interface driver registers | 18.127642 s |

The controller setup stall was **17.082835 seconds**. Enabling host USB and
CDC ACM did not resolve it; ACM driver registration does not establish that
the failed controller can enumerate a Pico. The boot firmware reported the
same version/hash as the earlier capture. This result does not isolate the
underlying reset/readiness failure or establish a general firmware/hardware
diagnosis.

Fresh UART bytes, capture hash/timings, and post-test controller status are
saved locally under `.local/usb-host-timeout-check-2026-10-22/` as
`boot-uart.log`, `result.json`, and `status-after.json`. The capture begins at
host UART byte offset 409088, recorded in `start.json`, excluding older boots.
UART errors remained at the pre-test count of 1; overruns, dropped/lost bytes,
and USB transmit failures remained zero. The Pi was left powered on. No flash,
recovery operation, configuration change or image build was performed by this
test. Successful USB enumeration and graphics exchanges remain unverified.

#### Firmware fix hardware test — 2026-10-22

Raspberry Pi maintainers identified a missing VL805 firmware decompressor in
`start4.elf`: USB can fail when the EEPROM bootloader has not already loaded
the controller firmware. See [firmware issue #2026](https://github.com/raspberrypi/firmware/issues/2026)
and [the upstream fix](https://github.com/raspberrypi/firmware/commit/0e05e17767d62a60cc2fafaafbdc42aaead3984b).
The issue was reported with secure boot, but the missing decoder also affects
other boot paths that leave VL805 initialization to `start4.elf`.

Using the repaired USB recovery connection, we backed up the actual SD boot
partition and replaced only `start4.elf` and `fixup4.dat` with the pair from
commit `0e05e17767d62a60cc2fafaafbdc42aaead3984b`. Changed sectors were confined
to that partition; its complete contents and the unchanged MBR passed direct
readback verification. Rootfs, data, kernel, DTBs, overlays and EEPROM were
not modified. The firmware now reports `2026-05-08T18:13:05`, hash
`ae9a8ea9f3ca745de6f357bd7fc8307721ad38b7`.

| Fresh normal boot | xHCI starts | Both root hubs initialized | Elapsed | Result |
| --- | --- | --- | --- | --- |
| Original firmware | 1.044681 s | Failed at 18.127516 s | 17.082835 s | `can't setup: -110` |
| Fixed firmware | 1.049255 s | 1.051751 s | 2.496 ms | USB 2/3 root hubs and onboard VIA hub enumerate |
| Fixed firmware, five-second power-off interval | 1.030393 s | 1.032796 s | 2.403 ms | Same success, no timeout |

This isolates the fix to the boot firmware pair and is consistent with the
upstream decompressor diagnosis. Enabling kernel USB support alone did not
fix the problem. The Pi is left powered on in Normal mode with the fixed
pair installed. Serial logs and EROFS/userspace startup were observed; a
graphics Pico exchange, UART3 controls and display/capture operation were
not exercised in this timeout test.

Evidence is under `.local/usb-host-timeout-check-2026-10-22/`:
`card-boot-before.img` is the rollback backup; `card-firmware-test.json`
records deployment and direct readback; `fixed-boot-uart.log`,
`repeat-boot-uart.log`, and their `*-result.json` files record the two boots.
The generic daemon `run` waited for `POLIS_SMOKE_READY init`, which this
profile does not emit. That marker-only observation was intentionally
interrupted by restarting the daemon; its journal is not a readiness pass.
The subsequent direct `cycle --off-ms 5000` completed. The controller
reconnected after a transient framing error during restart; UART errors
remained at 1 and UART overrun/drop/loss counters remained zero. USB transmit
failures rose during the restart and remained at 24 through the repeat test.

The maintained selection is in `profiles/groups/rpi4-usb-host.toml` and
`board/rpi4/vl805-firmware.mk`, with SHA-256 pins in
`board/rpi4/vl805-firmware.hash`. Buildroot downloads the pair and installs it
after the original firmware package. Incremental tracking invalidates the
firmware source/install steps when this selection or its pin changes, including
when disabling the override. Other profiles keep their original firmware.

Validation passed: isolated Buildroot `olddefconfig`, real package-variable
expansion, official Buildroot hash checks, enabled/disabled install-hook
checks, 34 focused profile tests, and 18 incremental-build tests. No image, kernel or toolchain build
was run during that experiment. At that point `.output/images/sdcard.img`
still contained the old firmware; the hardware test changed the SD card directly. To produce a
reproducible updated image, run:

```sh
make image ARGS="--profile polis-rpi4-screen-dev"
```

The resulting image is `.output/images/sdcard.img`; the firmware pair is in
`.output/images/rpi-firmware/`. The subsequent user-built image was tested below.

#### Rebuilt fixed-image acceptance — 2026-10-22

After the user rebuilt and flashed the image, an SBC-controller
`cycle --off-ms 5000` completed in Normal mode. Fresh UART capture began at
host byte offset 540394. The running firmware reported the expected
`2026-05-08T18:13:05` version and
`ae9a8ea9f3ca745de6f357bd7fc8307721ad38b7` hash.

| Event | Kernel uptime |
| --- | --- |
| xHCI setup starts | 1.023632 s |
| USB 2 root hub initialized | 1.025535 s |
| USB 3 root hub initialized | 1.026028 s |
| Onboard VIA hub initialized | 3.179805 s |
| EROFS root mounted | 3.352125 s |

Both root hubs initialized in **2.396 ms**, with no `can't setup: -110`.
Userspace reached Dropbear startup. UART error counters did not increase:
errors remained at 1, overruns/dropped/lost bytes at zero, and controller USB
transmit failures at 24. The Pi was left powered on in Normal mode. No
recovery, flash, firmware replacement or build was performed during this check.
This verifies the timeout fix in the newly flashed image; it does not verify
graphics Pico exchanges, UART3 controls or display/capture operation.

The local `.output/images/sdcard.img` has SHA-256
`dde4a1a30b53c0cf0dae542915f959354a1a1bae30c5f614f9e250cda9270332`.
Reading its FAT partition confirmed both firmware files match the pinned
hashes. This is a local artifact check, not a whole-card readback claim.
Evidence is in `.local/usb-host-rebuilt-check-2026-10-22/`: `start.json`,
`boot-uart.log`, `result.json`, `status-after.json`, and `local-image.json`.

### Verification on the rebuilt Pi

1. Connect the production controller to a USB-A port and check CDC binding:

   ```sh
   dmesg | grep -Ei 'brcm-pcie|xhci|usb|cdc_acm'
   readlink -f /sys/class/tty/ttyACM0/device/driver
   ```

   Substitute the actual tty number. Its interface driver should be `cdc_acm`.
   A missing tty after xHCI failure is a controller initialization problem.
2. Confirm `/usr/bin/hdmi-graphics-hello`, `/usr/sbin/polis-graphics-usb` and
   `/etc/init.d/S09polis-graphics-usb` are absent. There must be no mdev rule
   launching the retired supervisor.
3. From the application repository run `cargo make passthru`. Confirm the
   controller receives real HDMI facts and Tile artwork. Unplug/replug USB and
   verify the same passthru process reconnects. It selects exactly one matching
   controller unless `--controller-port` is explicit.
4. Recheck HDMI/KMS, CSI video, I²S/ALSA audio, Ethernet, Wi-Fi, SSH and the UART0
   development console. Legacy UART3 checks remain below.

Host validation (from the repository root):

```sh
PYTHONPATH=tests python3 -m unittest test_screen_development test_controller_usb test_profile_inputs test_profiles test_incremental
python3 -m src.profiles.schema --check
```

These checks do not establish that a new image builds or boots; physical
acceptance requires the rebuilt image.

## Pico control surface on UART3

This section covers legacy controller hardware. The production controller uses
the single USB connection owned by `hdmi-passthru` for controls, HDMI facts and
artwork.

The reusable `rpi4-uart3` group is selected by `polis-rpi4-screen-dev`.
It emits **`dtoverlay=uart3`**, installs the official firmware overlays in
normal boot artifacts, retains DT overlay symbols and requires the existing
built-in PL011 driver. It adds no target package, daemon, getty, console, or
runtime-overlay utility itself. The shared `development-access` group separately
installs the approved `dtoverlay` utility and libfdt in both dev images.
UART baud/framing are configured by the application
on each open, not by `config.txt`.

| Connection | Pi 4 header | Pico header |
| --- | --- | --- |
| Pi TX → Pico RX | BCM4, physical **7** | GP21 RX, physical **27** |
| Pi RX ← Pico TX | BCM5, physical **29** | GP20 TX, physical **26** |
| Common ground | GND, e.g. physical **6** | GND, e.g. physical **28** |

Use **3.3 V UART logic**, **115200 baud, 8N1**, with hardware and software
flow control off. GP20/21 use the Pico's UART1 function; the Pico firmware
must configure those pins. Omit `ctsrts` and all `rs485` parameters: the
[official BCM2711 uart3 overlay](https://github.com/raspberrypi/linux/blob/21b410140c47ffab5668399f6f143c7d7b935c8b/arch/arm/boot/dts/overlays/uart3-overlay.dts)
enables BCM4/5 only by default. Do not connect RS232 voltage levels.

UART0 stays on BCM14/15 with `console=ttyAMA0,115200` and the existing
`miniuart-bt` Bluetooth routing policy. TC358743 capture/audio, I²S BCM18–20,
and the BCM17 button are unchanged. This control link is separate from the
repository's SBC power/debug fixture, which uses Pico GP0/1 and Pi UART0.
No existing BCM4/5 consumer was found in the selected profile, rootfs services,
or enabled pinctrl references in its composed DTB. Other HATs, external
applications, and live GPIO claims still need checking on the actual board.
In particular, do not enable `w1-gpio` on its usual BCM4 pin alongside UART3.

For a compatible custom profile using `premerge-overlays`, the image plan
records every selected overlay, including parameters and order. The host
merger applies all of them to `bcm2711-rpi-4-b.premerged.dtb`; that file is
installed as `bcm2711-rpi-4-b.dtb` on the boot partition. Generated `config.txt`
omits those `dtoverlay` lines, and the boot partition omits the overlay
directory because their effects are already in the DTB. The shipped screen
profile uses normal firmware application. Its USB-host selection conflicts
with the existing USB-diagnostics prerequisite of `premerge-overlays`; do not
add that optimization to the screen profile without reviewing its composition.

### Identify the application device after boot

Run these checks as root on the Pi, after booting the rebuilt image. Linux
registration and aliases can change: **do not assume `/dev/ttyAMA3` or
`/dev/serial0`**. Identify UART3 by its device-tree node at
`/soc/serial@7e201600` (BCM2711 bus address; Linux resources may show
`fe201600`). Match that node to the tty's sysfs device or an ancestor:

```sh
DT=$(readlink -f /proc/device-tree)
UART3_NODE=/soc/serial@7e201600
if [ -r "$DT/__symbols__/uart3" ]; then
    UART3_NODE=$(tr -d '\000' < "$DT/__symbols__/uart3")
fi
printf 'UART3 node: %s, status: ' "$UART3_NODE"
tr -d '\000' < "$DT$UART3_NODE/status"; echo

find_uart3() {
    for tty in /sys/class/tty/ttyAMA*; do
        device=$(readlink -f "$tty/device") || continue
        while [ -n "$device" ] && [ "$device" != /sys ] && [ "$device" != / ]; do
            if [ "$(readlink -f "$device/of_node")" = "$DT$UART3_NODE" ]; then
                printf '/dev/%s\n' "${tty##*/}"
                break
            fi
            device=${device%/*}
        done
    done
}
UART3=$(find_uart3)
printf 'UART3 tty: %s\n' "$UART3"
test -c "$UART3"  # must succeed with exactly one device before proceeding
cat /proc/cmdline
cat /sys/class/tty/console/active
grep -v '^#' /etc/inittab | grep getty
```

Expect status `okay`, one tty mapped to UART3, only the existing UART0 serial
console, and no active getty entry in this profile (the last grep returns 1).
Stop if the discovered UART3 tty is listed as a console or used by a getty.
If it is absent, inspect `dmesg | grep -Ei 'uart|ttyAMA|pl011|pinctrl'`;
a DT status of `okay` alone does not prove a working serial device.

For live pin ownership, use the kernel's existing debugfs support:

```sh
grep -q ' /sys/kernel/debug debugfs ' /proc/mounts ||
    mount -t debugfs none /sys/kernel/debug
grep -E 'pin (4|5|17|18|19|20) \(' /sys/kernel/debug/pinctrl/*/pinmux-pins
cat /sys/kernel/debug/gpio
```

Before temporary activation, BCM4/5 must have no competing claim. After
activation, expect UART3/PL011 to own BCM4/5; verify the BCM17 button and I²S
owners remain as expected for the running application.

### Runtime smoke test

Stop the application that owns UART3 before opening it for a test. Configure
the Pico for UART1 GP20 TX / GP21 RX, 115200 8N1, no flow control, with a
known echo-test handler. The following payload is for that handler, not an
unspecified production control protocol. Use the `UART3` discovered above:

```sh
# Run in a subshell so errors cannot leave the test fd open in your shell.
(
    set -e
    test -c "$UART3"
    saved=$(stty -F "$UART3" -g)
    trap 'stty -F "$UART3" "$saved"' EXIT
    exec 3<>"$UART3"
    stty -F "$UART3" raw 115200 cs8 -parenb -cstopb \
        -crtscts -ixon -ixoff -ixany clocal cread -hupcl -echo min 0 time 10
    stty -F "$UART3" -a
    printf 'pico-uart3-smoke\n' >&3
    dd bs=256 count=1 <&3 2>/dev/null | od -An -tx1
    exec 3>&-
)
```

Expect the echoed bytes for `pico-uart3-smoke\n`; a read may return a partial
reply. `min 0 time 10` bounds a read waiting for its first byte to one second,
using existing BusyBox tools (this image has no `timeout` applet). Repeat the
read or use the application's framed protocol test to collect a full reply.
No bytes means check cross-connected TX/RX, ground, Pico firmware and pin
ownership. A successful echo checks both directions; still verify ordinary
application messages and UART0 console, capture/audio and button operation.
The application must set raw mode, `B115200`, `CS8`, `CREAD`/`CLOCAL`, clear
`PARENB`, `CSTOPB`, `CRTSCTS`, `IXON`, `IXOFF`, `IXANY`, and leave RS485 disabled
when it opens the port. Do not use serial-console options to set its baud.

### Can the existing image run `dtoverlay uart3` temporarily?

**The existing local image lacks `dtoverlay`; the next build adds it.** Both
dev profiles now select `/usr/bin/dtoverlay` and the separately approved libfdt
library under `/usr/lib`, using the pinned Raspberry Pi utils source. No
`dtc` programs, `dtparam`, target `dtmerge`, or additional services are selected.
Inspection of the pre-change local screen artifacts on 2026-10-19 found:

| Prerequisite | Existing artifact evidence |
| --- | --- |
| Target `dtoverlay` | Absent from the existing `.output/target`; the next build selects `BR2_PACKAGE_POLIS_DTOVERLAY=y` and `BR2_PACKAGE_DTC=y` (library only). The host-only merger does not provide it. |
| Official `uart3.dtbo` | Present in `.output/images/rpi-firmware/overlays/` and inside `.output/images/boot.vfat` at `/overlays/uart3.dtbo`. |
| Overlay kernel support | `CONFIG_OF_DYNAMIC=y`, `CONFIG_OF_OVERLAY=y`, `CONFIG_OF_CONFIGFS=y`, `CONFIG_CONFIGFS_FS=y` in the existing effective kernel config. |
| UART driver | `CONFIG_SERIAL_AMBA_PL011=y`; the pinned Pi DT uses `arm,pl011-axi`, supported by that driver's platform binding. |
| Access to boot overlays | The base fstab does not mount the boot partition. Mount it read-only or supply an explicit overlay directory. |

These are checks of existing local artifacts, not proof of what an attached
Pi is running. `BR2_LINUX_KERNEL_DTB_OVERLAY_SUPPORT` provides DT symbols for
merging; it alone does not establish runtime configfs support. The shared dev
group now explicitly requires the four runtime kernel settings above, already
enabled in the pinned defconfig. `dtoverlay` and libfdt were explicitly approved
for both dev images; further target dependencies still need approval under
`AGENTS.md`. Rebuild before treating the utility as available on the target.

Repeat the host checks without changing or building an image:

```sh
find .output/target \( -name dtoverlay -o -name 'libfdt.so*' \) -print
ls -l .output/images/rpi-firmware/overlays/uart3.dtbo
.output/host/bin/mdir -i .output/images/boot.vfat ::/overlays/uart3.dtbo
grep -E '^CONFIG_(OF_DYNAMIC|OF_OVERLAY|OF_CONFIGFS|CONFIGFS_FS|SERIAL_AMBA_PL011)=' \
    .output/profile/effective-kernel.config
```

On a Pi **without persistent UART3 already enabled**, such as a rebuilt
headless dev image, run the following temporary test once `dtoverlay` is available.
First check the node/status and BCM4/5 ownership as above; do not reapply the
overlay to an already-enabled UART3. These commands do not edit boot config:

```sh
command -v dtoverlay  # stop if absent
# If available, verify the RUNNING kernel config; otherwise use its build record.
if [ -r /proc/config.gz ]; then
    zcat /proc/config.gz | grep -E '^CONFIG_(OF_DYNAMIC|OF_OVERLAY|OF_CONFIGFS|CONFIGFS_FS|SERIAL_AMBA_PL011)='
fi
mkdir -p /run/uart3-boot
mount -t vfat -o ro /dev/mmcblk0p1 /run/uart3-boot
test -r /run/uart3-boot/overlays/uart3.dtbo
grep -q ' /sys/kernel/config configfs ' /proc/mounts ||
    mount -t configfs none /sys/kernel/config
test -d /sys/kernel/config/device-tree/overlays
dtoverlay -d /run/uart3-boot/overlays -l
dtoverlay -v -d /run/uart3-boot/overlays uart3
dtoverlay -d /run/uart3-boot/overlays -l
dmesg | tail -40
```

With the boot partition mounted at the utility's default `/boot` or
`/boot/firmware`, the activation command can instead be `dtoverlay uart3`.
Rerun device discovery and the smoke test; success requires a tty and actual
bidirectional traffic, not just a successful overlay command. A premerged boot
partition intentionally lacks `.dtbo` files, so this temporary procedure is
for a normal, non-premerged image. Never try to remove a boot-time/premerged
overlay. For a runtime-loaded test overlay, close all UART3 users and use
`dtoverlay -d /run/uart3-boot/overlays -l` to identify its name/index before removing that
specific overlay; do not use a blanket remove command.

### Validation and next build

Host tests cover profile isolation, preserved console/Bluetooth policy,
required configuration checks, normal overlay packaging, and premerge order,
parameters and error handling. Both dev profiles require `dtoverlay`, library-only
libfdt and runtime kernel support; base/speed selections remain unchanged.
A host-only merge of existing artifacts with
and without UART3 changed only `/soc/serial@7e201600/status` to `okay`;
its pin group remained exactly BCM4/5 with no RS485 property. No enabled
BCM4/5 pinctrl consumer existed before that merge. A second merge checked
UART3 with the existing `disable-bt`/`dwc2,dr_mode=peripheral` pair.
No image/kernel build or hardware test was performed for this change.

Run the next build yourself:

```sh
make image ARGS="--profile polis-rpi4-screen-dev"
```

Review `.output/profile/config.txt` for `dtoverlay=uart3` and the preserved
UART0/capture settings. Build outputs are `.output/images/sdcard.img`,
`.output/images/boot.vfat`, `.output/images/rpi-firmware/overlays/uart3.dtbo`,
and `.output/profile/audit.txt`. Compilation, boot and Pico communication
remain unverified until that build and the hardware checks above complete.

## Board assumptions and kernel details

Use a Raspberry Pi 4B, a compatible TC358743 board on its two-lane CSI camera
connector, and a monitor on the Pi's HDMI output. The capture board's HDMI is
the input; the Pi's HDMI connectors are outputs. Start with an unencrypted
720p60 source. Bandwidth, cable integrity and the capture board determine which
higher modes work; four-lane settings are for compatible Compute Modules.

Firmware uses `vc4-kms-v3d,cma-256`, `tc358743,media-controller`, then
`tc358743-audio`, with automatic camera/display overlays disabled.
CMA reserves 256 MiB for shared DMA buffers.
The Pi kernel pinned by this repository binds `brcm,bcm2835-unicam` to
`CONFIG_VIDEO_BCM2835_UNICAM_LEGACY`, including its media-controller mode.
The upstream `CONFIG_VIDEO_BCM2835_UNICAM` driver instead matches
`brcm,bcm2835-unicam-upstream`; enabling only that driver leaves this overlay
without capture. Recheck this pairing when updating the kernel.

VC4 requires `SOUND`, `SND` and `SND_SOC` in this kernel even for video use.
Capture, I2C, VC4, V3D, BCM2835 I2S, simple-card and the capture stub codec
drivers are built in. VC4's HDMI codec supplies playback; the separate
`tc358743-audio` overlay supplies capture. No `noaudio` KMS parameter is used,
and legacy `dtparam=audio=on` is unnecessary for KMS HDMI playback.
The HDMI DDC controller (`CONFIG_I2C_BRCMSTB`) must also be built in: this
image uses devtmpfs without automatic module loading. Leaving it as a module
keeps VC4 waiting for its I2C adapters, preventing display and HDMI audio
registration even though the V3D render device can appear.
The base's static-core verification remains enabled, with
shared libraries available for graphics and application binaries. Mesa uses
VC4 for display buffer management and V3D for rendering; see
[Mesa's driver documentation](https://docs.mesa3d.org/drivers/v3d.html).

## Bring-up over SSH

Run on the Pi as root after booting the image. Device numbers are examples:
discover the actual nodes before assigning the variables below. Budget about
15 minutes for these checks after connecting the hardware.

1. **Discover capture devices.**

   ```sh
   polis-dev-check
   dmesg | grep -Ei 'tc358743|unicam|vc4|v3d|cma'
   v4l2-ctl --list-devices
   for m in /dev/media*; do media-ctl -d "$m" -p; done
   ```

   Select the media graph containing `tc358743`, its `/dev/v4l-subdevN` node,
   and its connected Unicam `/dev/videoN`. Missing TC358743 indicates an
   overlay, power, ribbon/I2C, or driver-probe problem. Inspect `dmesg` before
   changing userspace libraries.

2. **Advertise an EDID and configure HDMI capture.**

   ```sh
   MEDIA=/dev/media0
   SUBDEV=/dev/v4l-subdev0
   VIDEO=/dev/video0
   v4l2-ctl -d "$SUBDEV" --set-edid=file=/data/capture-edid.bin,format=raw
   v4l2-ctl -d "$SUBDEV" --query-dv-timings
   v4l2-ctl -d "$SUBDEV" --set-dv-bt-timings=query
   v4l2-ctl -d "$SUBDEV" --get-dv-timings
   ```

   Supply a valid EDID from your capture-board vendor or an independently
   validated EDID restricted to supported modes; upload it to the indicated
   path first. Do not blindly copy a 4K monitor EDID. Reconnect the source or
   make it re-read EDID, then explicitly select 1280x720 at 60 Hz. A no-signal
   or no-lock error must be resolved before streaming.

   Use the **entity name and source pad reported by `media-ctl -p`** and the
   detected dimensions. Example for a bridge named `tc358743 10-000f`:

   ```sh
   media-ctl -d "$MEDIA" --set-v4l2 '"tc358743 10-000f":0 [fmt:UYVY8_1X16/1280x720 field:none]'
   v4l2-ctl -d "$VIDEO" --list-formats-ext
   v4l2-ctl -d "$VIDEO" --set-fmt-video=width=1280,height=720,pixelformat=UYVY
   v4l2-ctl -d "$VIDEO" --stream-mmap=4 --stream-count=120 --stream-to=/dev/null
   ```

   Confirm the returned format and successful frame capture. Reconfigure
   timings and both formats whenever the source mode changes. This MMAP smoke
   test proves capture only; it does not prove DMA-BUF interoperability.

3. **Discover display and rendering devices.**

   ```sh
   ls -l /dev/dri
   for c in /sys/class/drm/card[0-9]; do
       printf '%s: ' "$c"; readlink -f "$c/device/driver"
   done
   modetest -M vc4 -c -p
   ```

   Identify the VC4 primary card with a connected HDMI connector and a valid
   mode. V3D's render node performs GPU rendering; a render node cannot own
   KMS scanout. Do not assume `card0` is VC4. Empty connectors usually mean the
   wrong DRM device, a cable/monitor issue, or missing KMS overlay.

4. **Exercise EGL/GLES, GBM and atomic KMS.**

   ```sh
   /etc/init.d/S90dev-app stop
   kmscube -A -D /dev/dri/card1
   ```

   Replace `card1` with the discovered VC4 card. Confirm a visible rotating
   cube and a V3D renderer; stop with Ctrl-C. A software renderer is not an
   acceptable result. `Permission denied`/`Device busy` calls for checking
   DRM master ownership and other display processes. Restore your application
   with `/etc/init.d/S90dev-app start` when finished.

5. **Verify Vulkan discovery.**

   ```sh
   ls /usr/share/vulkan/icd.d
   vulkaninfo --summary
   ```

   Expect a Broadcom/V3D physical device through the V3DV ICD. No desktop
   display is expected; surface-related notices do not by themselves mean
   device enumeration failed. Missing ICD files or no physical device is a
   failure. Record outputs, the image checksum and the Pi serial alongside
   the audit; none of these host configuration checks implies hardware success.

## I²S wiring and EP-0214 uncertainty

The CSI ribbon carries video, **not the TC358743's I²S audio signals**. Connect
the bridge's digital audio output separately to the Pi 4B's 40-pin header.
These are signal names, not a claim about the EP-0214 pad order:

| TC358743 board signal | Pi BCM GPIO / function | Physical header pin |
| --- | --- | --- |
| BCK / SCK (bit clock output) | GPIO18 / PCM_CLK | 12 |
| LRCK / WFS (word/frame clock output) | GPIO19 / PCM_FS | 35 |
| DATA / SD / SD0 (audio data output) | GPIO20 / PCM_DIN | 38 |
| GND | Ground | 39 (or another GND pin) |

The bridge supplies both clocks; the Pi consumes them. The overlay specifies
two 32-bit I²S slots and uses `linux,spdif-dir` as a generic capture codec: the
wire protocol remains I²S, not S/PDIF. GPIO21/PCM_DOUT is not needed for this
capture path, and OSCK/MCLK is not connected by this overlay. Reserve the
PCM pins from other HATs, SPI1 and PWM uses. The wiring follows the pinned
[Raspberry Pi overlay README](https://github.com/raspberrypi/linux/blob/21b410140c47ffab5668399f6f143c7d7b935c8b/arch/arm/boot/dts/overlays/README);
clock roles and slots are defined in the
[audio overlay](https://github.com/raspberrypi/linux/blob/21b410140c47ffab5668399f6f143c7d7b935c8b/arch/arm/boot/dts/overlays/tc358743-audio-overlay.dts).
The README's `tc358743-fast` reference is historical; the pinned overlay source
requires the ordinary `tc358743` overlay, which this profile already loads.

**GeeekPi EP-0214 remains unverified for audio on our hardware.** The
[manufacturer wiki](https://wiki.52pi.com/index.php?title=EP-0214) identifies
the TC358743XBG, advertises solder pads and instructs loading the audio overlay.
It does not establish, for the actual board revision in hand, whether the
necessary I²S nets are routed to usable pads, whether required audio circuitry
is populated, or what voltage/level shifting those pads use. No verified
revision-specific audio schematic or successful capture measurement was
available for this implementation. Do not infer audio functionality from
working CSI video or the chip's capabilities alone, and do not substitute a
Geekworm/C790 board pinout for this GeeekPi board.

Before connecting, power down and verify the pad labels, routing and I/O levels
against a schematic or vendor confirmation for your revision. The Pi header
uses [3.3 V GPIO](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio);
do not connect 5 V signals or assume that a bare 1.8 V audio output meets the
Pi 4 input threshold. Use suitable level translation if required. Software
cannot repair missing traces, inaccessible signals or absent board components.

## ALSA hardware verification

Allow about 10 minutes after confirming the wiring. Run over SSH as root.
Use an HDMI display/receiver with audio support and enable its speakers.
Card indices can change: use the actual ALSA card IDs shown by discovery.

1. **Discover both audio paths.**

   ```sh
   cat /proc/asound/cards
   arecord -l
   aplay -l
   dmesg | grep -Ei 'asoc|i2s|snd|tc358743|hdmi'
   ```

   Expect a capture card named `tc358743` and VC4 HDMI playback cards such as
   `vc4hdmi0`/`vc4hdmi1`. Card registration alone does not prove connected I²S
   wiring, HDMI audio lock, or valid samples. A missing capture card calls for
   checking the audio overlay and ASoC probe messages. A missing HDMI card
   calls for checking VC4/HDMI codec probing and the KMS overlay.

2. **Verify HDMI output independently.** Upload a known-good stereo PCM WAV
   to `/data/known-good.wav`, then select the connected port's discovered card:

   ```sh
   PLAYBACK='plughw:CARD=vc4hdmi0,DEV=0'
   aplay -D "$PLAYBACK" /data/known-good.wav
   cat /proc/asound/card*/eld*
   ```

   Confirm audible output and sink audio capabilities in ELD. If playback is
   silent, check the physical port, monitor volume and HDMI EDID/ELD before
   debugging capture. `plughw` allows ALSA format conversion for this smoke
   test; it is not a low-latency application configuration.

3. **Prepare the input.** Complete the video EDID/timing setup above. The input
   EDID must advertise stereo LPCM audio at the intended sample rate. Set the
   source to stereo PCM, initially 48 kHz, rather than encoded Dolby/DTS audio.

   ```sh
   v4l2-ctl -d "$SUBDEV" --list-ctrls
   v4l2-ctl -d "$SUBDEV" --log-status
   dmesg | tail -60
   ```

   Check `audio_present` and `audio_sampling_rate` where exposed. Match the
   capture rate to the detected input rate: the generic capture codec does not
   negotiate it with the HDMI receiver.

4. **Record with video streaming active.** The pinned TC358743 driver's
   `enable_stream()` toggles both video and audio buffer enable bits. Keep the
   configured video stream running during this check; stop any competing
   application first. Use the `VIDEO` node discovered above:

   ```sh
   CAPTURE='hw:CARD=tc358743,DEV=0'
   v4l2-ctl -d "$VIDEO" --stream-mmap=4 --stream-to=/dev/null > /data/audio-video-test.log 2>&1 &
   VIDEO_PID=$!
   arecord -D "$CAPTURE" --dump-hw-params -t wav -f S32_LE -c 2 -r 48000 -d 5 /data/hdmi-capture.wav
   kill "$VIDEO_PID"
   wait "$VIDEO_PID"
   ```

   Use supported parameters and the detected input rate if they differ from
   this example. Clockless I²S can stall capture; interrupt `arecord` with Ctrl-C
   if it does not finish, then run the cleanup commands. Inspect the video log
   if capture never starts. For silence or corrupt samples, verify source PCM
   audio, EDID, BCK/LRCK/data signals, electrical levels and sample alignment.

5. **Listen to the captured file.**

   ```sh
   aplay -D "$PLAYBACK" /data/hdmi-capture.wav
   ```

   Confirm recognizable audio without distortion and record card IDs, source
   format/rate, board revision, wiring and image checksum. These manual checks
   are still required after a successful build. Capture and playback use
   independent clocks; live forwarding needs buffering, drift management and
   A/V synchronization in the application. No automatic audio forwarding
   service or fixed default ALSA card is installed.

## FFmpeg libraries and SDK integration

Only `polis-rpi4-screen-dev` selects `ffmpeg-libraries`. It uses the existing
Buildroot **FFmpeg 6.1.5** recipe, source hash and patches without a version
override. `../2050-hdmi-passthru/tools/codec.py` is the functionality reference;
its private FFmpeg 8.0.1 build is not used by this image or SDK.

The enabled libraries are `libavcodec`, `libavformat`, `libavutil` and
`libswresample`: native software H.264/AAC/MP3/AC3 decoding, MOV/MP4 demuxing,
the `file` protocol and audio resampling. The matching H.264, AAC, MPEG audio
and AC3 parsers are enabled. This does not enable raw MP3/AC3 file demuxers,
encoding, muxing, network input, hardware decoding, capture devices, filters
or video scaling. ALSA/V4L2 capture remains application-owned.
`ffmpeg`, `ffplay` and `ffprobe` are disabled, as are GPL/nonfree options.
External-library autodetection is disabled, along with the recipe's explicit
libv4l2/libdrm/zlib integrations. No additional target dependency is selected.
These switches use the upstream
[FFmpeg 6.1.5 configure interface](https://github.com/FFmpeg/FFmpeg/blob/n6.1.5/configure).

Build the image with the command near the top of this guide. Buildroot's
existing `FFMPEG_INSTALL_STAGING = YES` installs matching development files;
no separate FFmpeg build or target development package is needed. The existing
shared/static policy produces shared runtime libraries for the image and both
shared and static libraries for cross-compilation:

| Location after a successful build | Contents |
| --- | --- |
| `.output/target/usr/lib/` and the image's `/usr/lib/` | `libavcodec.so.60`, `libavformat.so.60`, `libavutil.so.58`, `libswresample.so.4` and their versioned files/symlinks |
| `.output/staging/usr/include/lib{avcodec,avformat,avutil,swresample}/` | Headers from the same FFmpeg build |
| `.output/staging/usr/lib/` | Matching `.so` linker files and `.a` archives |
| `.output/staging/usr/lib/pkgconfig/` | `libavcodec.pc`, `libavformat.pc`, `libavutil.pc`, `libswresample.pc` |

Use the SDK from this same image build. `.output/staging` points into
`.output/host`; it is the cross sysroot, not a directory to copy onto the Pi.
After building, run these checks on the development host from this repository:

```sh
export POLIS_OUTPUT="$PWD/.output"
export PKG_CONFIG_ALLOW_CROSS=1
export PKG_CONFIG_SYSROOT_DIR="$POLIS_OUTPUT/staging"
export PKG_CONFIG_LIBDIR="$POLIS_OUTPUT/staging/usr/lib/pkgconfig:$POLIS_OUTPUT/staging/usr/share/pkgconfig"
export PKG_CONFIG="$POLIS_OUTPUT/host/bin/pkg-config"
unset PKG_CONFIG_PATH
"$PKG_CONFIG" --modversion libavcodec libavformat libavutil libswresample
"$PKG_CONFIG" --cflags --libs libavformat libavcodec libswresample libavutil
```

The reported numbers are individual library ABI versions, not `6.1.5` for
every library. For a C application, use `.output/host/bin/aarch64-linux-gcc`
and the flags above, placing link flags after source/object files. Query
`pkg-config --static --libs` when linking the archives so private dependencies
are included. For Rust, use the environment in the next section and a binding
crate compatible with FFmpeg 6.1; disable any crate feature that downloads or
builds its own FFmpeg. Configure the consumer of `codec.py` to use this sysroot
instead of invoking `codec.prepare()` or its `.local/ffmpeg` prefix. Do not
compile against 8.x headers and deploy against the 6.1 libraries.

For a relocatable SDK, after the image build you may run `make -C .output sdk`.
This is a **build command**, not a configuration check: Buildroot's `sdk` target
depends on `world`. It writes `.output/images/aarch64-buildroot-linux-musl_sdk-buildroot.tar.gz`
with the default SDK prefix. Extract it on the development host, run its
`relocate-sdk.sh`, and use its `bin/aarch64-linux-gcc -print-sysroot` result for
`PKG_CONFIG_SYSROOT_DIR` and the pkg-config search paths. See the
[Buildroot SDK documentation](https://buildroot.org/downloads/manual/manual.html#_using_the_generated_toolchain_outside_buildroot).

Configuration and host tests do not verify installed libraries or playback.
After a successful build, check the four `.pc` files and library SONAMEs with
the SDK tools, confirm that `/usr/bin/ffmpeg`, `/usr/bin/ffplay` and
`/usr/bin/ffprobe` are absent, then test MP4 decoding and resampling through
your application on the Pi. No image build or hardware acceptance is implied.

## Cross-compile Rust against the staging libraries

Build the image successfully first. In your application repository, use the
same output's compiler and sysroot; host `/usr/lib` must not enter target links.
Install the Rust target on the development host if it is not already present.

```sh
rustup target add aarch64-unknown-linux-musl
POLIS_OUTPUT=/absolute/path/to/BuildrootImages/.output
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER="$POLIS_OUTPUT/host/bin/aarch64-linux-gcc"
export CC_aarch64_unknown_linux_musl="$POLIS_OUTPUT/host/bin/aarch64-linux-gcc"
export AR_aarch64_unknown_linux_musl="$POLIS_OUTPUT/host/bin/aarch64-linux-ar"
export PKG_CONFIG_ALLOW_CROSS=1
export PKG_CONFIG_SYSROOT_DIR="$POLIS_OUTPUT/staging"
export PKG_CONFIG_LIBDIR="$POLIS_OUTPUT/staging/usr/lib/pkgconfig:$POLIS_OUTPUT/staging/usr/share/pkgconfig"
unset PKG_CONFIG_PATH
export PKG_CONFIG="$POLIS_OUTPUT/host/bin/pkg-config"
"$PKG_CONFIG" --modversion egl glesv2 gbm libdrm vulkan alsa libavcodec libavformat libavutil libswresample
CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_RUSTFLAGS='-C target-feature=-crt-static' \
    cargo build --release --target aarch64-unknown-linux-musl
```

Rust's musl target normally links the C runtime statically. Disabling
`crt-static` for this application allows Mesa/Vulkan shared libraries and
dynamic driver loading; it does not change the image's static-core policy.
Clear conflicting `RUSTFLAGS`/`CARGO_ENCODED_RUSTFLAGS` in the application's
environment. If a crate runs bindgen, install host libclang and pass its
`--target=aarch64-linux-musl --sysroot=$POLIS_OUTPUT/staging` arguments through
the crate's bindgen configuration. No target Rust compiler or libclang is needed.
Inspect the result with the output's `aarch64-linux-readelf -l -d` before using
the existing development guide's SCP update procedure.

The application still must implement buffer lifetime and synchronization:
export V4L2 MMAP allocations with `VIDIOC_EXPBUF`, import compatible planes
with `EGL_EXT_image_dma_buf_import`, render into GBM buffers, and submit atomic
KMS commits. Check formats, pitches, offsets and modifiers across each boundary.
Do not return a capture buffer to V4L2 until GPU access is complete, or reuse a
scanout buffer before its page-flip completion. See the kernel's
[DMA-BUF API documentation](https://www.kernel.org/doc/html/latest/userspace-api/media/v4l/dmabuf.html).
Successful `kmscube` and MMAP capture tests alone establish neither zero-copy
operation nor end-to-end latency.
