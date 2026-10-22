# Host overlay merger

The `premerge-overlays` profile group selects `BR2_PACKAGE_HOST_POLIS_DTMERGE`.
Buildroot builds `host-polis-dtmerge` against `host-dtc`'s libfdt and installs
`dtmerge` in `HOST_DIR/bin`. Image assembly runs this executable to merge the
ordered `firmware_overlays` list from `profile/image-plan.json`, including each
overlay's comma-separated parameters. This includes application UART3 when
selected, as well as Bluetooth/UART and USB peripheral overlays. It no longer
compiles tools from target package or kernel source directories. Firmware
overlay directives are omitted from the premerged boot config to avoid applying
them twice; the merged DTB carries their effects without a boot overlay directory.

This package uses pinned Raspberry Pi utils source and installs nothing into
the Pi image. `polis-boot-diagnostics` provides target-side `vclog`
and `vcgencmd` through its internal boot-diagnostics-tools recipe. Selecting
overlay merging does not select those utilities. The speed profile still gets
them through `boot-diagnostics`.
