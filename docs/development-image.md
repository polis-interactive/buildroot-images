# Pi 4 development image

Build `polis-rpi4-dev` in **BuildrootImages**. It inherits the musl toolchain,
read-only EROFS root and ext4 `/data` layout. Ethernet uses the built-in BCMGENET
and Broadcom PHY drivers with DHCP; iwd owns Wi-Fi association and addressing.
The development profile includes system D-Bus, Pi Wi-Fi firmware, `iwctl`,
key-only Dropbear SSH, and Avahi. It does not select `no-ethernet`.

## Build and identify

```sh
BUILDROOT_IMAGES=/absolute/path/to/BuildrootImages
BUILDROOT=/absolute/path/to/buildroot-2026.08
make -C "$BUILDROOT_IMAGES" image \
  ARGS="--profile polis-rpi4-dev --development --buildroot $BUILDROOT --downloads $BUILDROOT_IMAGES/dl"
```

The first build generates an Ed25519 identity at
`.local/development-ssh/id_ed25519`, with mode 0600 in a mode-0700 directory.
The private key remains on the host in ignored `.local/`; it is never an overlay,
snapshot member, or image input. The public key is included and fingerprinted.
To use an existing public key, pass `--ssh-public-key /absolute/path/key.pub`
to every staged configure/build/snapshot command. A private-key file is rejected.
Keep the generated private key to access this image on later boots.

`--development` is the existing provisioning-mode flag: it skips EEPROM setup
and automatic data-partition expansion. The **profile** selects the development
packages. Selecting this flag on the speed profile does not add SSH or Ethernet.
Use the same mode and public key when rebuilding. No build command flashes a card.

All profiles use [`profiles/buildroot.lock.json`](../profiles/buildroot.lock.json),
which pins only the Buildroot source archive and input-tree digest. Buildroot
already pins the toolchain, kernel and package versions/checksums; these are not
copied into our lock. The fixed filesystem timestamp comes from the locked
Buildroot Makefile's `BR2_VERSION_EPOCH`. `BR2_DOWNLOAD_FORCE_CHECK_HASHES` and
`BR2_REPRODUCIBLE` apply to every profile. See [shared inputs and rebuilds](build-profiles.md#shared-input-lock-and-rebuilds).
Use the same public key and checkout/output paths when comparing rebuilds.
A clean build in another path has a different audited plan identity.

The command prints the output and read-only snapshot directories. In the snapshot:

```sh
sha256sum images/sdcard.img
sha256sum -c SHA256SUMS
cat profile/build-inputs.json
```

`profile/build-inputs.json` records the BuildrootImages Git revision, dirty status,
plan digest, shared Buildroot lock and derived build timestamp. `profile/audit.json`
records source hashes and effective configuration checks; saved Buildroot and
component configurations record the resolved toolchain and package selections. The snapshot also contains the source
archive and effective Buildroot/kernel/BusyBox configurations. A dirty checkout is
identified honestly; downstream consumers should pin a committed revision before
claiming a clean reproducible release.

## Initial installation — separate and destructive

Installing the initial SD image needs a separately selected card and explicit
confirmation. Use the existing `make flash` workflow only after inspecting
`make devices`; select the built snapshot and intended card. `--expand-data` can
expand `/data` during this initial installation. **Do not flash for binary updates.**

No physical boot, Ethernet SSH or reboot acceptance is implied by a successful
build. The intended Pi must boot this installed SD image before those checks.

## Find and connect

The hostname is `polis-dev`. Avahi advertises **`polis-dev.local` on Ethernet**
using the DHCP-assigned address; there is no fixed IP in the image. mDNS must be
supported on the client and multicast allowed on the same local network.
See [Avahi's mDNS documentation](https://avahi.org/).

```sh
getent hosts polis-dev.local
ssh -i "$BUILDROOT_IMAGES/.local/development-ssh/id_ed25519" root@polis-dev.local
```

On Linux, `avahi-resolve-host-name polis-dev.local` can distinguish mDNS discovery
from a missing resolver integration. If the network blocks mDNS, look for
`polis-dev` in the router's DHCP leases and pass that address to SSH. Do not add a
static address to the image. If multiple development Pis are connected, Avahi
may rename a conflicting host; confirm the intended board's serial before changes.

SSH passwords are disabled at compile time and with `-s`; no login getty is
started. `/root/.ssh` points to `/data/config/ssh`, and Dropbear host keys are
created per device under `/data/config/dropbear`. Both survive reboot. The initial
public key is copied once; subsequent local edits to `authorized_keys` persist.
Wi-Fi credentials are provisioned only at runtime; none are baked into the image.

## Hardware acceptance

Record the SD image checksum, snapshot path, expected Pi serial and the following
outputs in the consuming project's test record. Check the physical board identity
against the serial shown by `/proc/device-tree/serial-number` before rebooting.

1. Connect over Ethernet SSH and run `polis-dev-check`. This checks AArch64,
   hostname, read-only root, writable ext4 data, persistent `/var/lib`, Ethernet
   carrier, system D-Bus, the running iwd object manager, `iwctl`, and Wi-Fi device
   discovery. Record model, serial and `/etc/polis-development-image.json`.
2. Create a persistence marker and record identities:
   ```sh
   printf '%s\n' "acceptance-$(cat /proc/sys/kernel/random/boot_id)" > /var/lib/iwd/.polis-persistence-test
   cat /var/lib/iwd/.polis-persistence-test
   cat /proc/sys/kernel/random/boot_id
   cat /var/lib/dbus/machine-id
   sha256sum /data/config/dropbear/*_host_key
   sync
   ```
3. Run `reboot` on the confirmed Pi. Reconnect with the same SSH command and
   normal host-key verification. Record `polis-dev-check` again, a changed boot
   ID, and unchanged marker, D-Bus machine ID and SSH host-key checksums.

`/var/lib` is bound from `/data/var-lib` before services start, so iwd network
profiles survive reboot. D-Bus machine identity also persists. The application
and logs are on `/data`; runtime sockets and PIDs stay in `/run`.

## Routine application updates

The development image starts `/data/bin/dev-app`, if present, using
`/etc/init.d/S90dev-app`. The application must remain in the foreground; the
service's working directory is `/data`. It has no automatic crash restart.
stdout/stderr are in `/data/log/dev-app.log`; the preceding run is retained as
`dev-app.previous.log`. For continuously bounded logging, send application logs
to syslog: `/data/log/messages` rotates at 256 KiB with three backups. Direct
stdout logs can grow during a long run, so inspect or rotate them during testing.

Build binaries for `aarch64-unknown-linux-musl` with the recorded compatible
toolchain. Upload a new file, stop the service, rename it atomically, then start it:

```sh
PI_HOST=polis-dev.local
SSH_KEY="$BUILDROOT_IMAGES/.local/development-ssh/id_ed25519"
scp -O -i "$SSH_KEY" ./your-aarch64-musl-binary "root@$PI_HOST:/data/bin/dev-app.new"
ssh -i "$SSH_KEY" "root@$PI_HOST" \
  '/etc/init.d/S90dev-app stop && chmod 755 /data/bin/dev-app.new && mv /data/bin/dev-app.new /data/bin/dev-app && /etc/init.d/S90dev-app start'
```

`scp -O` uses the SCP protocol supported by Dropbear; this image has no SFTP server.
This update does not rewrite the SD image or touch iwd state.

## Consuming repository contract

Parameterize `BUILDROOT_IMAGES`, `BUILDROOT`, `PI_HOST` and `SSH_KEY` in the
consuming repo's development tooling. Reference profile `polis-rpi4-dev` and the
existing `make image` entry point. Record the BuildrootImages revision, dirty
state if any, toolchain, plan digest and SD image SHA-256 from the snapshot.
Keep image profiles, kernel configuration, overlays and pins here; the consuming
repo owns application builds and SSH uploads, not another image definition.
