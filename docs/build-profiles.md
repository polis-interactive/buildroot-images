# Build profiles and audit format

Polis profiles select named groups of changes on top of a shared Pi 4 base.
The base is **musl, EROFS, and enforced static linking of core executables**.
An audit is always saved. A complete automatic build prints its location, not
the differences; staged configuration displays the differences before building.

The CLI consumes [`polis-rpi4-base`](../profiles/polis-rpi4-base.toml),
[`polis-rpi4-dev`](../profiles/polis-rpi4-dev.toml),
[`polis-rpi4-screen-dev`](../profiles/polis-rpi4-screen-dev.toml), and
[`polis-rpi4-speed`](../profiles/polis-rpi4-speed.toml).
Base now includes the user-tested Wi-Fi integration pinned to wifi-setupd 0.1.3
and its release portal; see the [package guide](../package/polis-wifi-setupd/README.md).
The speed profile selects the optimized kernel, firmware, image layout and
EEPROM settings and excludes Wi-Fi. Base provides `polis.local` without SSH or
Ethernet DHCP.
Dev selects the same Wi-Fi stack plus Ethernet and
SSH, retaining its headless settings and separate hostname. These profiles all
compose groups over `polis-rpi4-v1`; they do not inherit other profile TOML files.
Screen-dev reuses dev's feature groups and replaces `headless` with `rpi4-video`
for direct KMS graphics and TC358743 capture. Its shared graphics libraries
coexist with the static-core policy; see the [bring-up guide](screen-development-image.md).
Commands and EEPROM preparation are documented in the
[README](../README.md#declarative-build-profiles).

Current implementation: group resolution, owned explicit changes, input hashes,
Buildroot selection checks, generated configs, static ELF checks, image assembly,
staged execution guards and snapshot audits. Kernel/BusyBox selections are checked
against their effective configs during build. **Full dependency-induced effective
comparison and attribution are not implemented yet**: coverage stays pending,
and `configured` currently certifies the requested plan and Buildroot selections.
The complete-effective-coverage requirements below remain the target contract;
the current audit does not claim to satisfy them.

## Shared input lock and rebuilds

Every profile uses [`profiles/buildroot.lock.json`](../profiles/buildroot.lock.json).
It contains the Buildroot source URL, archive SHA-256, and a digest of the
Buildroot files used by this repository. Configuration checks the supplied tree
against that digest, including when reusing an existing configuration. A changed
Buildroot tree requires an explicitly reviewed lock update; it is not silently
accepted based on a matching release name or defconfig.

The lock does not repeat Buildroot's package, firmware, kernel or toolchain pins.
Those remain in Buildroot's recipes and hashes, together with the selected profile
configuration. The build timestamp is derived from `BR2_VERSION_EPOCH` in the
locked Buildroot Makefile. `BR2_REPRODUCIBLE` is inherited by all profiles; FAT,
ext4 and the speed profile's nested boot image use stable filesystem metadata.
The timestamp controls output bytes, not source change detection.

Input fingerprints use file contents, permissions and symlink destinations.
Touching a file does not select a new output directory or invalidate cached
component rules. Only the selected profile and selected groups' definitions and
payloads enter the profile-input portion of the fingerprint; editing an unselected
profile or its overlay does not invalidate another profile. Shared build code,
packages, board support and patches remain conservatively tracked across profiles.
All profiles, modes and input revisions use `.output/`. Source changes refresh
affected components in place; snapshots preserve completed images. After
reconfiguration the next build clears only `target/` and target-install stamps,
then reinstalls selected packages using their existing compiled objects. This
removes obsolete packages and overlay files without discarding the build cache.

Repeated configuration reuses a verified unchanged `.config` and audit instead of
rerunning defconfig. Generated fragments are written only when their contents
change. Component rules use their selected configuration and declared inputs;
they no longer include every profile's overlays as global compilation inputs.
Final validation/image assembly still runs when invoking `build` or `image`.

Each configuration saves `profile/build-inputs.json` with the shared lock, derived
epoch, source revision/dirty status and plan digest. The existing audit, effective
component configs and snapshot source archive retain the build evidence. The
public-key digest is included only for profiles with development SSH access.
Changing Git revision or file mtimes alone is not a compilation input.

Input fingerprints are cache and provenance metadata, never output-directory
names. No profile or input change creates a new default build directory.
Build commands stay the same; no full builds are run during agent validation.

## The comparison base

`polis-rpi4-v1` is our meaning of stock for new profiles. Its declaration is
[examples/base.toml](build-profiles/examples/base.toml), with a
[strict schema](build-profiles/base-v1.schema.json). Its three required groups
are `musl-toolchain`, `erofs-root`, and `static-core`. They remain separately
auditable even though every profile using this base inherits them.

The base starts from Buildroot 2026.08's `raspberrypi4_64_defconfig` and its
pinned Raspberry Pi sources. The resolver must record the actual Buildroot
source identity and hashes of all relevant configuration inputs; a release
label alone does not establish that a local tree is unchanged. The existing
`rpi4_stock` benchmark board is preserved as historical evidence, not redefined
by this new use of stock.

`erofs-root` owns EROFS kernel support, LZ4HC filesystem compression at level 9,
the read-only root command line, writable runtime mounts, and persistent ext4
data storage. Kernel payload compression is a separate optimization. This
group must not inherit unrelated boot diagnostics, Wi-Fi, or headless settings from the
existing combined overlays.

`static-core` uses the linking overrides and incremental rules in
`build-support/static-core/`, together with the kmod patch. It requests
both static and shared libraries, statically links core executables, and verifies
the resulting ELF files. BusyBox is required. When their packages are selected,
iwd, D-Bus tools, kmod, XZ tools, the boot collector, and firmware diagnostic utilities
must satisfy the static policy. Missing expected executables or an ELF interpreter
or `DT_NEEDED` entry fails verification. Dynamic linking remains available to
other packages; this is not global `BR2_STATIC_LIBS=y`.

`check-static.py` reads Buildroot's selected packages to determine the checked
executable set. The shared `polis-rpi4-v1` core does not enable Wi-Fi or the
collector to satisfy the checker; the base image selects Wi-Fi explicitly.
The static check runs after all profile post-build hooks.

`premerge-overlays` selects the host-only `polis-dtmerge` package, built against
Buildroot's host libfdt. It does not select target diagnostic utilities.
`boot-diagnostics` owns `vclog` and `vcgencmd` through its internal boot-diagnostics-tools
recipe under `package/polis-boot-diagnostics/`; they have no separate selectable option.

Standard firmware is inherited from upstream. Camera/display restrictions,
benchmark instrumentation, USB role choices, and boot optimizations are not
implicit base settings. A profile that does not disable a capability does not
automatically have its drivers and userspace installed: capability requirements
must be verified explicitly.

## Handwritten profile

[examples/profile.toml](build-profiles/examples/profile.toml) shows the intended
short profile. [profile-v1.schema.json](build-profiles/profile-v1.schema.json)
validates the TOML data after parsing. All top-level fields are required;
unknown fields, duplicate selections, and unsupported schema versions fail.

The names are serialized Python enums, defined in
[`src/profiles/options.py`](../src/profiles/options.py): `Base`, `Group`, and
`Capability`. For example, `"lz4-kernel"` becomes `Group.LZ4_KERNEL` through
[`load_profile`](../src/profiles/loader.py). The loader returns an immutable typed
`Profile`; it rejects unknown names and base groups repeated as optional groups.
The resolver loads each enum's manifest from `profiles/groups/<group>.toml`,
checks dependencies/capability conflicts, and composes owned settings. Naming a
capability does not prove a hardware function; those checks stay separate.

Schema choice lists are generated from those enums with
`python -m src.profiles.schema`; `python -m src.profiles.schema --check` checks for
drift. Structural schema fields remain handwritten. The examples' `#:schema`
headers associate them with local schemas. Editors using Taplo can use these
for validation and enum completion; no executable Python configuration is needed.
The profile directive uses `profile.schema.json`, a symlink to the generated
`profile-v1.schema.json`, to bypass the previously cached schema URL in Even
Better TOML. The workspace sets `evenBetterToml.schema.cache.diskExpiration`
to `0` so subsequent reloads read schema updates from disk. Validation remains
enabled; regenerate enum choices with the command above when adding options.
See [Taplo schema directives](https://taplo.tamasfe.dev/configuration/directives.html).

| Field | Meaning |
| --- | --- |
| `schema` | Format version, currently integer `1` |
| `name` | Stable profile identifier; no output paths or timestamps |
| `base` | Named, versioned base whose required groups are inherited |
| `requires` | Capabilities the completed image must provide |
| `groups` | Explicit feature and optimization group selections |

Selection is set-like: list order does not decide who wins a conflict.
Unselected optional groups are disabled. Base groups cannot be disabled through
a profile; changing those foundations requires another versioned base.
Profiles contain no shell commands, expressions, arbitrary symbol overrides,
or configuration inheritance chains.

Schema validation and the typed loader check structure and enum membership.
The resolver must additionally require declared prerequisites,
reject cycles and incompatibilities, and reject two groups assigning different
values to the same setting. Identical assignments may retain multiple owners.
Dependencies are not silently enabled: a missing optional prerequisite reports
the group that requires it and the selection needed to resolve the error.

Every non-upstream setting and asset must have an owner: one of the base groups,
a selected group, or an explicitly recorded consequence of their settings.
An unowned effective difference blocks configuration acceptance. A dependency
explanation must be derived from the configuration system or an explicit rule;
do not invent a unique owner when several groups cause the same result.

## Group boundaries

Groups live together with their Buildroot/kernel/BusyBox fragments, firmware
settings, overlay files, and assembly rules. A group declares prerequisites,
capabilities it provides or removes, conflicts, validation, and evidence links.
Arbitrary executable code stays in reviewed implementation files, not profiles.
Each manifest has `schema`, `id`, `description`, optional `requires`, `provides`,
`disables`, `inputs`, `overlays`, `post_build`, and `evidence` arrays, and
`buildroot`, `kernel`, `busybox`, `firmware`, `cmdline`, and `eeprom` setting tables.
`remove_buildroot`, `remove_firmware`, `remove_cmdline`, `remove_overlays`, and
`firmware_overlays` explicitly adjust inherited inputs. Settings use strings;
Kconfig values are `"y"`, `"m"`, `"n"`, numbers, or string contents.

The implemented boundaries are:

| Group or family | Owns |
| --- | --- |
| Base groups | Musl toolchain, EROFS/read-only storage layout, static core policy |
| Product features | Application/runtime services, Wi-Fi, boot diagnostics, UART/USB diagnostics, benchmark instrumentation |
| `rpi4-uart3` | Official BCM2711 UART3 overlay on BCM4/5 for the Pico control surface; application-owned serial port, no console or getty. See [wiring and tests](screen-development-image.md#pico-control-surface-on-uart3). |
| `ffmpeg-libraries` | Upstream FFmpeg 6.1.5 libraries and staging development files for software H.264/AAC/MP3/AC3 decoding, MP4 file input and resampling. Selected only by `polis-rpi4-screen-dev`; no FFmpeg CLI programs or new external target dependencies. See [SDK integration](screen-development-image.md#ffmpeg-libraries-and-sdk-integration). |
| `rpi4-usb-host` | Pi 4 USB-A VL805/xHCI and built-in CDC ACM with firmware-reset prerequisites; excludes gadget diagnostics and unrelated USB peripheral drivers. |
| `screen-graphics-usb` | Existing BusyBox mdev events start one supervised `hdmi-graphics-hello` per matching Pico. Requires a prepared app payload; see [graphics USB](screen-development-image.md#graphics-usb-on-the-usb-a-host-ports). |
| Capability restrictions | Headless configuration, no Ethernet, UART/Bluetooth routing, USB peripheral-only operation, no input, no 32-bit compatibility/containers/alternate storage |
| Independent optimizations | Compiler size optimization, debug instrumentation trimming, no cron, HAT/PoE probe skips, LZ4 kernel, premerged overlays, boot ramdisk |
| Board provisioning | First-boot EEPROM configuration enabled by default; `--development` disables it. Storage is never resized on boot; expand `/data` during host provisioning. Capture-based `prepare-eeprom` / `image --capture` remain optional. |

Use descriptive identifiers; experiment IDs remain only as evidence references.
The CLI's `--development` mode is an input to the build fingerprint, applies to
staged configure/build/snapshot/audit as well as image. Both modes share
`.output/`; reconfiguration switches the active selection in place.
The target configuration and `images/provisioning.json` declare the mode;
automatic snapshots include `final` or `development` in their name.
First-boot status is captured separately from readiness and excluded from normal
benchmark samples. See [setup and recovery details](../README.md#first-boot-setup).
A measured bundle gain does not establish individual gains after splitting it
into groups. Cut-down firmware, EEPROM settings and boot ramdisk are selected
by `polis-rpi4-speed`; none is inherited by the base.
Premerged overlays and boot ramdisk are independently selectable: premerging must remove the matching
runtime overlay directives, while disabling premerge must restore them and
package the required overlays. Boot-image sizing depends on the selected
firmware and kernel payload, with a capacity check before assembly.

The example is illustrative; `profiles/` contains the executable selections.
Freshly composed images still need hardware qualification before claiming the
historical timing. Assembly with the saved optimized kernel reproduces its LZ4
payload, premerged DTB, cut-down firmware and 6 MiB boot ramdisk layout.

## Saved audit

Use JSON for the machine-readable `audit.json`, and render `audit.txt` from that
same object. The human report is never a second source of truth. The schema is
[audit-v1.schema.json](build-profiles/audit-v1.schema.json); the small synthetic
[example](build-profiles/examples/audit.json) illustrates its structure only.

An audit contains:

1. The profile, base, resolved groups, and content fingerprints of all inputs.
2. Separate `upstream-to-base` and `base-to-profile` comparisons.
3. Requested and effective changes, their owners, and dependency explanations.
4. Coverage and validation results, including checks still pending.
5. Hardware assumptions and hashes of any completed artifacts.

The main report displays `base-to-profile`; inspecting the base displays
`upstream-to-base`. Both are always stored. Differences include configuration
symbols, package selection, command line, firmware settings, rootfs overlays,
patches, hooks, and image layout. Referenced files are content-addressed inputs;
record a diff or a saved, content-addressed payload for changed text files and
record hashes for binary assets. Merely listing a hook's filename is not enough
to audit its behavior. External tool/source identities are inputs too.

Each change has a domain and key, a comparison, a stage (`requested` or
`effective`), before/after values, owning groups, and a reason. Values are
canonical strings; `null` means absent, never unknown. An unverified effective
value is omitted and represented by pending coverage, not a fabricated value.
For example, a disabled Kconfig symbol is `"n"`; a file value is its SHA-256.

The `basis` distinguishes a directly assigned setting from a dependency-derived
change. Derived changes must name their causative settings in `caused_by`.
Owners must reference resolved groups. Baseline groups have `selected_by=base`;
optional selections have `selected_by=profile`. Rejected inputs can produce
a failed audit but cannot produce an accepted configured plan.

The audit's lifecycle is `planned`, `configured`, `built`, or `failed`.
`planned` is an incomplete preview, not permission for a staged build.
`configured` requires complete effective configuration coverage and successful
configuration checks. Verification requiring binaries, including static-link
checks, stays pending until build. `built` requires successful artifact checks
and records image hashes; it never implies a hardware boot or timing result.
JSON Schema verifies shape, while the resolver must enforce these lifecycle,
reference, coverage, and ownership invariants.

Configuration coverage spans Buildroot, kernel, BusyBox, firmware, command line,
rootfs, image assembly, and EEPROM settings. Each domain records `pending`, `complete`, or
`not-applicable`; the latter needs a reason. Selecting no changes in a domain
does not remove the need to check it against the base. Package dependency and
Kconfig consequences must appear in the effective comparison, not only literal
fragment assignments.

Hardware assumptions such as EEPROM configuration are recorded with verification status and any
evidence. A build never applies EEPROM settings or claims an assumption was
verified just because the profile names it. Hardware validation is separate
from configuration and artifact validation.

## Execution behavior

| Mode | Audit and execution |
| --- | --- |
| Automatic | Configure, save audit, build, assemble, verify, prepare EEPROM artifacts, snapshot; print concise progress and audit location |
| Staged configuration | Resolve and validate; save and display the audit before returning |
| Staged build | Consume the accepted configuration; stop if its inputs or generated configs changed |
| Inspect | Render the saved report without configuring, building, or touching hardware |

The two build modes share one resolver and executor. Display is a CLI concern,
not a profile setting. Warnings and actionable failures remain visible in
automatic mode; successful differences are not dumped to the terminal.

The plan fingerprint covers the base, profile, resolved groups, source and tool
identities, referenced input contents, and resolver version. Generated config
hashes are recorded too. Staged execution checks both before using a plan and
again before recording success. A changed input requires configuration again;
automatic mode performs that step itself. Never silently regenerate a reviewed
staged plan or snapshot artifacts from a failed or stale plan.

Generated files stay under `.output/profile/` (or explicit `--output`). Package
removals repopulate the target rootfs; incompatible toolchain changes still need
a clean build in the same directory. Integrate
these decisions with the existing incremental build tracker and directory lock.

Save the profile, base declaration, resolved group inputs, generated configs,
audit JSON/text, and artifacts in the snapshot. Artifact hashes bind the audit
to the image. Keep benchmark results separate so historical timings cannot be
mistaken for measurements of a newly composed image.

## Delivery checks

The implementation is ready when the following behaviors are demonstrated:

1. A base-only profile contains musl, EROFS, and verified static core linking
   without inheriting headless restrictions or benchmark instrumentation.
2. A selected group explains every direct or induced difference, and misspelled
   identifiers, ownership gaps, and conflicting selections fail before compiling.
3. Automatic and staged execution produce equivalent resolved configurations;
   only staged configuration displays the audit by default.
4. Changing a fragment after staged configuration blocks execution; unchanged
   inputs preserve the existing incremental-build behavior.
5. Every successful snapshot contains the complete audit and verified artifact
   hashes, with pending hardware checks represented honestly.
