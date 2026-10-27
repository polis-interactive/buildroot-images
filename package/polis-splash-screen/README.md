# polis-splash-screen

A persistent Rust DRM client for `polis-rpi4-screen-dev`, built into the pinned
Raspberry Pi Linux **6.12.61** tree (`21b410140c47ffab5668399f6f143c7d7b935c8b`).
Buildroot is **2026.08**. This is a splash, not a terminal emulator.

The profile disables fbdev (`CONFIG_FB` and `CONFIG_FB_CORE`), DRM fbdev emulation,
fbcon, VT and the old kernel logo. It retains simpledrm, VC4 and V3D. The first
splash is drawn by VC4; there is no early simpledrm logo. Serial logging stays on.
No userspace service, library or interpreter is added to the target.

## Ownership and restoration

VC4 registers the client after `drm_dev_register()`, instead of calling
`drm_fbdev_dma_setup()`. DRM immediately calls hotplug and serializes all client
callbacks with `clientlist_mutex`.

Every redraw attempts `drm_master_internal_acquire()`. If an application owns
master, it leaves the display alone. Otherwise, it holds the internal master lock
through probing, rendering and the blocking atomic commit, then releases it.
Applications acquire DRM master and modeset normally, without a splash protocol.

The DRM `restore` callback runs on **lastclose**, just as for a kernel fbdev
client. It probes again to handle monitors changed while the application was
running. Closing one application does not restore the splash if another process
still holds a userspace DRM file on the same VC4 device open. A V3D render file
on the separate V3D device does not hold VC4 open. `DROP_MASTER` alone is not a
restore event.
This is not a compositor, a general VT switch replacement, or suspend/resume
support; the pinned client API has no PM callbacks.

There is one XRGB8888 DRM scanout buffer per enabled CRTC, with a centered 512×512
logo on black. Small displays scale the logo down with nearest-neighbor sampling.
Modes are capped at 4096×2160, with at most eight CRTC slots (VC4 has fewer), and
each padded allocation is capped at 64 MiB. An ordinary 1920×1080 output uses
about 7.9 MiB; 3840×2160 uses about 31.6 MiB. Redraw temporarily retains both old
and new buffers. The persistent RGB artwork uses 768 KiB. These DRM buffers do
not require the legacy fbdev subsystem or expose `/dev/fb*`.

Failed allocation/commit leaves the old scanout buffers alive. Desired modeset
pointers are detached before candidate buffers are freed. Disconnect commits
disable outputs and release their buffers. Unregister detaches buffers, deletes
them, releases the internal DRM file, then frees the Rust allocation. Hotplug
always returns zero to DRM because this kernel permanently disables subsequent
hotplug callbacks after a nonzero return. Errors are logged once per changed
error code and retried at the next hotplug or lastclose; there is no retry timer.

## Private Rust bindings

`kernel.patch` adds the Kconfig/Kbuild entries, the VC4 registration call, and
conditional bindgen includes. `drm_internal.h` supplies this kernel's private
master-lock declarations. C struct layouts and function signatures are generated
by the kernel's own bindgen invocation; none are reproduced by hand.

`src/drm.rs` owns the unsafe adapter and buffer/mapping/mutex guards.
`src/polis_splash_screen.rs` owns the C callback trampoline and stable allocation.
`src/lifecycle.rs` owns the safe display policy, and `src/render.rs` renders pixels.
Only the small registration call/declaration and header/build integration are C;
new behavior and the host preparation tool are Rust.

On a kernel upgrade, review the client callback/locking contract, master API,
buffer ownership, generated types, and VC4 integration, then repeat the hardware
checks. Rust protects the safe policy layer; it does not prove the FFI contracts.

## Host prerequisites and build

The kernel Rust compiler needs its matching `rust-src`, plus bindgen and libclang.
The hook prefers `rustup which rustc` to avoid Buildroot's host Rust installation,
which lacks rust-src. It uses a locally installed bindgen if available, otherwise
one on PATH. The current host prerequisite check passes with Rust 1.99.0,
bindgen 0.72.1 and libclang 21. This does **not** establish kernel build compatibility.

The integration also separates Buildroot's host C compiler from its flags:
`HOSTCC` is a single executable, with flags passed through `HOSTCFLAGS` and
`HOSTLDFLAGS`. Kbuild passes `HOSTCC` to Rust as `-Clinker=...`; embedding `-O2`
there makes rustc fail with `Unrecognized option: '2'` while building `libmacros.so`.
The host regression test compiles a tiny procedural macro using this command
shape and verifies that the C include and linker paths are retained.

For another host, install these host-only prerequisites:

```sh
rustup component add rust-src
cargo install bindgen-cli --version 0.72.1 --locked --root "$PWD/.local/polis-splash-host"
```

Install your distribution's libclang development package if necessary. Nothing
from this host setup is installed in the image. Non-rustup installations may set
`POLIS_SPLASH_RUSTC=/absolute/path/to/rustc` and
`POLIS_SPLASH_BINDGEN=/absolute/path/to/bindgen` in the build environment;
`RUST_LIB_SRC` and `LIBCLANG_PATH` are also honored by the kernel tools.

`CONFIG_MODVERSIONS=n` is required by this pinned kernel's Rust support. Rebuild
modules with the same kernel; binary modules relying on modversion checks are
outside this profile's support.

The post-patch hook applies the integration before Kconfig runs, installs the
Rust source and persistent artwork. The pre-configure hook checks `rustavailable`
after Buildroot has installed its toolchain dependencies. The pre-build hook
refreshes source/artwork without changing timestamps for identical files and
checks the host Rust prerequisites again.
The preparation tool refuses partially applied or incompatible patches.
Incremental tracking cleans Linux sources for integration-patch changes or a
splash enable/disable transition. Rust/artwork edits reconfigure Linux and keep
unchanged object files.

Build when ready:

```sh
make image ARGS="--profile polis-rpi4-screen-dev"
```

Image: `.output/images/sdcard.img`. Effective config:
`.output/profile/effective-kernel.config`. Splash changes alone do not require
new target packages or changes to the graphics executable preparation.

## Validation

Host renderer, lifecycle, patch idempotence and partial-patch rejection tests:

```sh
PYTHONPATH=tests:. python3 -m unittest tests.test_splash_screen tests.test_screen_development tests.test_profile_builds tests.test_incremental
```

An optional check generates real ARM64 bindings from an already configured,
pinned kernel tree and type-checks the splash with a host Rust API shim:

```sh
POLIS_SPLASH_TEST_KERNEL=.output/build/linux-custom \
  PYTHONPATH=tests:. python3 -m unittest tests.test_splash_screen
```

The shim lives only in `tests/`; it is never copied into the kernel. This check
validates the generated C interface and Rust source types, **not** the real kernel
crate, linking, DMA coherency, lock ordering or display hardware. A complete image
build and these hardware checks remain necessary:

1. Boot with HDMI attached: centered logo, serial logs, no HDMI console.
2. Boot without HDMI, wait until userspace runs, then connect: logo appears.
3. Unplug/replug and repeat with both HDMI outputs: correct sizing and no stale image.
4. Run a direct DRM application normally, change HDMI while it runs, then exit:
   no competing splash redraw; logo returns after DRM lastclose.
5. Check `dmesg` for splash registration, redraw errors, lock warnings and crashes;
   confirm `/dev/fb*` is absent and the earlier USB timeout remains absent.
