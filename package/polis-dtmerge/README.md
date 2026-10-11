# Host overlay merger

The `premerge-overlays` profile group selects `BR2_PACKAGE_HOST_POLIS_DTMERGE`.
Buildroot builds `host-polis-dtmerge` against `host-dtc`'s libfdt and installs
`dtmerge` in `HOST_DIR/bin`. Image assembly runs this executable to merge the
Bluetooth/UART and USB peripheral overlays; it no longer compiles tools from
target package or kernel source directories.

This package uses pinned Raspberry Pi utils source and installs nothing into
the Pi image. `polis-boot-diagnostics` provides target-side `vclog`
and `vcgencmd` through its internal boot-diagnostics-tools recipe. Selecting
overlay merging does not select those utilities. The speed profile still gets
them through `boot-diagnostics`.
