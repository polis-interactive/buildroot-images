# Polis Buildroot images

`BR2_EXTERNAL` tree for Buildroot **2026.08**. The `polis_rpi4_defconfig`
starts from that release's `raspberrypi4_64_defconfig` and targets a
Raspberry Pi 4 Model B with a microSD card.

## Hardware and baseline

- AArch64, the upstream pinned Raspberry Pi kernel and Bootlin toolchain.
- BusyBox init, an ext4 root filesystem, and the upstream SD-card image generator.
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
connection. Runtime network profiles are stored in `/var/lib/iwd` on the writable
ext4 root filesystem and survive reboot. There is no credential-copy hook or
build-time credential file. The `iwctl` client is omitted to avoid adding readline;
the provisioning application will control iwd through D-Bus.

## Configure and build

From this directory:

```sh
make list-boards
make BOARD=polis_rpi4 configure
make BOARD=polis_rpi4 build
```

`BOARD` is the defconfig filename without `_defconfig`. Board options are
discovered from `configs/*_defconfig`; currently `polis_rpi4` is the only option
and the default. New defconfigs appear automatically. Keep the same `BOARD=`
for `configure`, `build`, `menuconfig`, and `savedefconfig`.

The wrapper defaults to `/home/broosegoose/Software/buildroot-2026.08`. Override
it with `make BUILDROOT=/path/to/buildroot ...`. The default Pi 4 build keeps
using `output/rpi4/`; other boards use `output/<board>/`. Downloads reuse
`$(BUILDROOT)/dl`, leaving the source tree's current
configuration and output alone. `O=` and `DL_DIR=` can override those locations. Downloads
require network access and a first build compiles the kernel and host tools.

Rebuilds in the same output directory reuse completed package builds. A fresh
output directory compiles them again. The shared download cache avoids
downloading the same sources. To use a different cache:

```sh
make DL_DIR=/path/to/download-cache build
```

Use that same `DL_DIR=` on subsequent wrapper commands. Compiler caching
(`BR2_CCACHE=y` in menuconfig) can help later rebuilds, but does not cache
compilation performed before it was enabled.

The completed Pi 4 image is `output/rpi4/images/sdcard.img`.

Equivalent native Buildroot configuration command:

```sh
make -C /home/broosegoose/Software/buildroot-2026.08 \
    O="$PWD/output/rpi4" BR2_EXTERNAL="$PWD" \
    BR2_DL_DIR=/home/broosegoose/Software/buildroot-2026.08/dl \
    polis_rpi4_defconfig
```

## Flash an SD card

```sh
make list-images
make list-devices
make flash
```

`list-images` discovers `sdcard.img` under `images/` in this repo's output
directories, `$(BUILDROOT)/output`, and the selected `O=` directory. Duplicate
paths are listed once. `list-devices` shows disks and partitions with their size,
model, removable/read-only flags, and mount points.

`make flash` lists source images, asks for an image number and a whole-disk
destination, then requires you to type the destination path to confirm erasing
it. It refuses partitions, read-only devices, disks with mounted filesystems or
active swap, and disks smaller than the image. Unmount the card's partitions
before flashing. The script requests sudo for the write if needed and flushes
the image to disk before reporting completion.

You can supply the source and destination directly; confirmation is still
required in an interactive terminal:

```sh
make flash IMAGE=/path/to/sdcard.img DEVICE=/dev/sdX
```

Replace `/dev/sdX` with the card's actual whole-disk device. Flashing never
starts a build. The helper uses Bash and standard Linux tools; no Python or uv
environment is needed.

## Customize

```sh
make menuconfig
make savedefconfig
```

`make configure` reloads the saved defconfig and replaces unsaved menuconfig
changes. `make savedefconfig` writes back to `configs/<BOARD>_defconfig`. Commands
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
generated output Makefile to reconfigure that component:

```sh
make -C output/rpi4 linux-reconfigure
make -C output/rpi4 busybox-reconfigure
make build
```

Use a fresh output directory after removing packages to avoid stale files in
the target filesystem; for example `make O=output/rpi4-clean configure` followed
by `make O=output/rpi4-clean build`.

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
