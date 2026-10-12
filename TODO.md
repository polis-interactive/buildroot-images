# TODO

- [x] Apply profile EEPROM settings through default-on first-boot provisioning.

  Treat ordinary image builds as final images: enable first-boot provisioning by
  default, including profile EEPROM settings and `/data` expansion. Provide an
  explicit development opt-out that disables both. Build-time optimization loops,
  hardware debugging, and benchmark workflows must use that opt-out when producing
  development images, avoiding EEPROM writes, expansion, and provisioning reboots.
  Record the mode in image metadata and snapshot identification, and prevent a
  later development build from inheriting an updater or pending update files from
  a previous final build through reused output directories.

  Embed desired settings without requiring a Pi, saved diagnostics, or the host
  daemon. On the target, read its current configuration, preserve unrelated
  settings, and stage an EEPROM update only when needed. Save recovery information,
  verify the settings after reboot, and avoid repeated writes or reboot loops.
  Report provisioning separately from normal boot readiness and
  benchmarks; provisioning boots must not count as benchmark samples. This should
  work with both removable-SD and daemon-controlled flashing, with provisioning
  controlled by the image's recorded mode rather than the flashing method.

  Development images must leave the board's existing EEPROM settings unchanged;
  disabling provisioning does not undo settings applied by an earlier final image.

  Implemented with `--development` to skip setup. `make image` works without
  hardware; the optional capture-based EEPROM preparation flow remains available.
  EEPROM changes currently require Pi 4 Model B with the bundled 2025-11-05
  firmware; other releases are accepted only when settings already match.
  Host tests cover interrupted staging, verification failure, and repeat boots.
  A complete image build and on-board qualification remain to be run.

- [x] Expand `/data` by default through the shared first-boot provisioning routine.

  Run expansion as an independent step in the same first-boot routine as EEPROM
  provisioning, enabled by default and skipped by the development opt-out above.
  Expansion must still run when the profile has no EEPROM settings or the board
  already matches them. Track each step's completion separately across reboots.

  Grow the final `polis-data` partition and its ext4 filesystem to use the available
  card capacity, preserving existing data and the boot/read-only root partitions.
  Make repeated boots a no-op once expanded, and handle interrupted expansion
  without formatting or losing data. Support images written by any flashing tool,
  including daemon-controlled flashing; no host-side expansion is required.

  Host-side expansion already exists as `make flash ARGS="--expand-data"`.
  Keep it available as an explicit option; first-boot expansion must be a no-op
  if flashing already expanded the filesystem to fill the card.
  Implemented with MBR backup, online ext4 growth, and independent completion
  reporting. Host tests exercise interrupted expansion and preservation of files;
  physical SD-card and power-loss qualification remain to be run.
