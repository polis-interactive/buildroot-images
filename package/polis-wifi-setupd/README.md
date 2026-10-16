# polis-wifi-setupd

The package pins release **0.1.3**, including the published portal archive and
its verified SHA-256. Following user-confirmed successful integration testing,
the base and dev profiles both include it. The promoted base profile still
requires its own image build and boot verification.

The recipe compiles the daemon from tag `0.1.3` with Buildroot 2026.08's Rust 1.97.1 and
the image's AArch64 musl toolchain. Upstream CI supplies the matching portal.
The image build needs no Dioxus, WASM compiler or rustup installation. Upstream
removed `rust-toolchain.toml`; the recipe continues invoking Buildroot's Cargo
directly. Upstream reports a successful Rust 1.97.1/AArch64 musl cross-build in
its [integration guide](https://github.com/polis-interactive/wifi-setupd/blob/0.1.3/docs/buildroot.md).
That evidence does not replace validation of the integrated image.

## Verified release inputs

The [0.1.3 release](https://github.com/polis-interactive/wifi-setupd/releases/tag/0.1.3)
provides `wifi-setupd-web-0.1.3.tar.gz`, its `.sha256` file and
`.provenance.json`. The downloaded archive and provenance match both the
published checksum file and GitHub's asset digests. Provenance identifies the
tagged commit `cd33808197d32aeffee5cedbb9b09295099c7936`, a clean source tree,
the matching Cargo.lock hash, and the exact web-tool versions. CI used Rust
1.99.0, Dioxus 0.7.10, wasm-bindgen 0.2.129, esbuild 0.27.3 and Binaryen 120.

The archive contains `index.html` and `assets/` at its root and passes this
package's extraction allowlist and the upstream staging installer. The runtime
Rust sources and third-party locked dependencies are unchanged from 0.1.0.
Upstream's 21 host packaging/release tests pass for 0.1.3. No additional upstream
development was identified as necessary before the integrated image build.

`polis-wifi-setupd.hash` pins the portal archive, source archive and project
license. The source hash covers Buildroot's vendored `-cargo6` archive, generated
offline with Cargo 1.97.1, rather than GitHub's unprocessed archive. Buildroot
vendors the whole workspace, including portal dependencies, but compiles only
the daemon. Keep the daemon source and portal version together on future bumps;
verify the new provenance and regenerate the vendored source hash.

## Build with a supplied password

Release **0.1.3 stays pinned**. The updated integration contract is documented in
[commit b8e8c3e](https://github.com/polis-interactive/wifi-setupd/commit/b8e8c3e84d0dca68a0350d59d3e869f4e844aa97).
No new daemon interface or release is needed.

Supply the initial setup AP/portal-management password using either:

- `POLIS_WIFI_SETUP_PASSWORD` in the build environment; or
- `.local/wifi-setupd/password`, containing the password on one line.

The environment variable takes precedence. An explicitly empty value is an
error. Passwords must be 8–63 printable ASCII characters without spaces. No
password is generated automatically. Base and dev
require this input; speed ignores it. The `.local` directory is gitignored.

For example, create `.local/wifi-setupd/password` with your chosen default, then:

```sh
make image ARGS="--profile polis-rpi4-base --buildroot /home/broosegoose/Software/buildroot-2026.08"
```

Output: `.output/polis-rpi4-base-<input-hash>/images/sdcard.img`, plus the printed
snapshot under `.snapshots/`. All devices flashed from this image initially use
that password and setup SSID **Polis-Setup**. Base contains iwd, the portal and
Avahi, without Ethernet DHCP or SSH. Dev adds Ethernet DHCP, SSH and its
headless settings.

The password is a tracked image input: changing it changes the output identity,
and a staged build rejects changed inputs until reconfigured. A mode-0600 copy
is captured at `output/profile/wifi-password`; plaintext is not included in
configuration/audit JSON or command arguments. The image and its snapshots
intentionally contain the password as configuration, not in the executable.

## Persistent files and future device passwords

Image assembly populates the writable data partition before first boot:

| On the running device | Purpose |
| --- | --- |
| `/data/wifi-setupd/device-key` | Supplied AP/management password, mode 0600 |
| `/data/wifi-setupd/ssid` | Setup SSID (`Polis-Setup`) |
| `/var/lib/iwd/ap/Polis-Setup.ap` | Matching `Passphrase` and AP network settings |

`/var/lib` is backed by `/data/var-lib`. Image assembly also copies the packages'
initial `/var/lib` state, including the persistent D-Bus directory, into that
partition. Credential directories are private and their files root-owned.
The AP uses `DisableHT=true`, address `192.168.16.1/28`, and gateway/DNS `.1`.
iwd derives its WPA2 PSK from `Passphrase`; image assembly does not require
OpenSSL support in Buildroot's host Python. Backslashes are escaped for iwd's
settings reader so the AP and management password remain identical.
Home-router credentials entered through the portal remain iwd's own files.

Boot does not generate, rotate, or reinstall credentials. Updating firmware
while preserving `/data` preserves the installed password, even when a newer
image has another default. Flashing an entire SD image replaces `/data` and
therefore installs that image's password.

Future deployment tooling can replace `device-key` and the matching iwd AP
profile in the card's data partition before deployment. It must write and verify
both files while the card is offline (or the daemon and AP are stopped), preserve
unrelated router profiles, and escape backslashes in the AP `Passphrase`. After
replacement, only the replacement password is used for both purposes; there is
no fallback to the firmware default or one-time lock to work around. If changing
the SSID, update `ssid` and the AP filename together. Customer inventory,
password generation and database backup are intentionally future work.

## Startup and hostname

`/etc/init.d/S85wifi-setupd` uses the existing BusyBox `start-stop-daemon` to start
the Rust daemon directly in the background. It checks mounted storage and the
presence of the credential files. This package no longer requires target Python
or installs a readiness wrapper or password-generation command. The separate
base first-boot package still uses Python. The daemon already retries system D-Bus
connection and observes iwd availability, so boot does not wait for the radio.

```sh
/etc/init.d/S85wifi-setupd status
/etc/init.d/S85wifi-setupd restart
```

Status reports process liveness, not portal/radio readiness. Stop sends SIGTERM
and allows 35 seconds for cleanup without force-killing. Logs are private
`/data/wifi-setupd/daemon.log`, rotated to `daemon.previous.log` on start.
There is no automatic daemon crash restart.

Set `BR2_TARGET_GENERIC_HOSTNAME` in
[`wifi-identity.toml`](../../profiles/groups/wifi-identity.toml): `polis` becomes
`polis.local`. Dev sets `polis-dev` in
[`development-access.toml`](../../profiles/groups/development-access.toml).
Avahi advertises current IPv4 addresses on Wi-Fi and, in dev, Ethernet. The
portal accepts the system hostname; restart it after changing that hostname.

## Validation

If compilation and rootfs generation finished but the post-image hook failed,
rerun just the hook against that existing output directory (from the repository
root). Substitute the failed build's actual directory below:

```sh
out="$PWD/.output/polis-rpi4-base-<input-hash>"
BASE_DIR="$out" BR2_EXTERNAL_POLIS_PATH="$PWD" \
BINARIES_DIR="$out/images" HOST_DIR="$out/host" BUILD_DIR="$out/build" \
"$out/host/bin/python3" profiles/post-image.py
```

This reuses the captured password and compiled artifacts; it does not invoke
Buildroot or recompile the kernel for this profile. The result is
`$out/images/sdcard.img`. A manual retry does not mark the profile workflow's
failed audit as successful or create a certified snapshot. If source changed
since configuration, the normal workflow will still require a fresh configuration.

```sh
python3 -m unittest discover -s tests -p test_wifi_setupd.py
python3 -m unittest discover -s tests -p 'test_profile*.py'
make configure ARGS="--profile polis-rpi4-base --buildroot /home/broosegoose/Software/buildroot-2026.08"
```

Host tests cover supplied-password precedence, validation, matching credential
files, image staging and input tracking. Configuration checks do not establish
compilation, boot, phone onboarding or hardware persistence. After building,
verify AP access and portal management with the supplied password, reboot
persistence, and an update that preserves `/data`.
