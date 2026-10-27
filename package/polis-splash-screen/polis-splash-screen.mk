# Kernel extension only: no target package, daemon, interpreter or shared library.
ifeq ($(BR2_POLIS_SPLASH_SCREEN),y)
POLIS_SPLASH_DIR = $(BR2_EXTERNAL_POLIS_PATH)/package/polis-splash-screen
# Resolve host tools before Buildroot prepends its own Rust (without rust-src).
# Explicit overrides support installations without rustup.
POLIS_SPLASH_RUSTC ?= $(shell rustup which rustc 2>/dev/null || command -v rustc)
POLIS_SPLASH_BINDGEN ?= $(or $(wildcard $(BR2_EXTERNAL_POLIS_PATH)/.local/polis-splash-host/bin/bindgen),$(shell command -v bindgen))
LINUX_MAKE_FLAGS += RUSTC="$(POLIS_SPLASH_RUSTC)" HOSTRUSTC="$(POLIS_SPLASH_RUSTC)" BINDGEN="$(POLIS_SPLASH_BINDGEN)"
# Buildroot embeds host flags in HOSTCC. Kbuild's Rust proc-macro rule uses
# -Clinker=$(HOSTCC), so embedded -O2 becomes a rustc argument ("unknown option 2").
# Supply one linker executable and preserve the flags in Kbuild's own variables.
# These later assignments override Buildroot's combined HOSTCC on the command line.
LINUX_MAKE_FLAGS += HOSTCC="$(HOSTCC_NOCCACHE)" \
	HOSTCFLAGS="$(subst -I/,-isystem /,$(subst -I /,-isystem /,$(HOST_CFLAGS)))" \
	HOSTLDFLAGS="$(HOST_LDFLAGS)"

define POLIS_INSTALL_SPLASH_SCREEN
	@test -n "$(POLIS_SPLASH_RUSTC)" -a -n "$(POLIS_SPLASH_BINDGEN)" || \
		{ echo "polis-splash-screen: install host rustc + rust-src and bindgen; see package/polis-splash-screen/README.md"; exit 1; }
	$(POLIS_SPLASH_RUSTC) --edition=2021 -O $(POLIS_SPLASH_DIR)/prepare.rs -o $(@D)/.polis-splash-prepare
	$(@D)/.polis-splash-prepare $(BR2_EXTERNAL_POLIS_PATH) $(@D)
endef

define POLIS_CHECK_SPLASH_RUST
	$(LINUX_MAKE_ENV) $(MAKE) $(LINUX_MAKE_FLAGS) -C $(@D) rustavailable
endef
# Kconfig and generated-binding headers must be present before configuration.
LINUX_POST_PATCH_HOOKS += POLIS_INSTALL_SPLASH_SCREEN
# linux-patch can run before the cross compiler exists on a clean host. Kconfig
# checks Rust availability itself; the explicit diagnostic belongs after the
# toolchain dependency has been installed, not in the source-patching hook.
LINUX_PRE_CONFIGURE_HOOKS += POLIS_CHECK_SPLASH_RUST
# Refresh Rust/artwork inputs on incremental builds without discarding Linux
# objects. prepare preserves timestamps for unchanged payloads.
LINUX_PRE_BUILD_HOOKS += POLIS_INSTALL_SPLASH_SCREEN POLIS_CHECK_SPLASH_RUST
endif
