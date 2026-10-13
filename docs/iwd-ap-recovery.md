# iwd station-to-AP recovery

The iwd 3.12 package needs
`patches/musl/iwd/0001-network-unregister-bss-objects-on-removal.patch`.
It is included by the existing `musl-toolchain` global patch directory; that
group also fingerprints `patches/musl` as an image input.

When a station is removed, iwd removes Network objects and frees scan BSS data.
ELL does not recursively unregister a removed object's children. Unremoved
BasicServiceSet objects therefore retain dangling pointers, and a later
ObjectManager.GetManagedObjects call can crash in
`network_bss_property_get_address` / `util_address_to_string`.
The patch removes each network's BSS objects before removing the parent.

## Package-only build and live development test

On 2026-10-13, only iwd was rebuilt using the existing development image's
configured sources, object files, cross compiler and ELL 0.83 library. The
configured iwd tree was copied to `/tmp/wifi-setupd-iwd-bss-fix`, old `output/`
paths in Makefile/config.status were updated to `.output/`, and this patch was
applied. The build command in that directory was:

```sh
make -j2 src/iwd
```

The resulting static AArch64 binary is `src/iwd`. Its SHA-256 is
`7c0a6810ac5b4a59995e7fff1a1dba7f45ea7b2ef6f70d599835e75cf2719e85`.
This was a package-only rebuild, not an image/kernel/toolchain build.

The wifi-setupd development suite uploaded and checksum-verified that binary
over identity-checked Ethernet SSH. On the Pi it is stored at
`/data/wifi-setupd-portal/iwd-fix/iwd` and temporarily bind-mounted over
`/usr/libexec/iwd`. The original binary and checksum are retained alongside it.
The normal S40iwd service runs the replacement. Reboot removes this live mount;
a future image build must include the source patch to retain the fix.

To roll back the live replacement, from the wifi-setupd checkout:

```sh
python3 dev/pi/portal.py stop
python3 dev/pi/pi.py exec /etc/init.d/S40iwd stop
python3 dev/pi/pi.py exec umount /usr/libexec/iwd
python3 dev/pi/pi.py exec /etc/init.d/S40iwd start
python3 dev/pi/portal.py start
```

Rollback restores the original iwd behavior, including the known AP-transition
crash. Neither installation nor rollback reads or replaces station credentials.
Hardware evidence is recorded in the wifi-setupd checkout under
`.local/pi/iwd-fix-acceptance.log` and `docs/testing-plan.md`.
