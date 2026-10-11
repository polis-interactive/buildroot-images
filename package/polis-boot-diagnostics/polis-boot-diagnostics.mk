################################################################################
#
# polis-boot-diagnostics
#
################################################################################

POLIS_BOOT_DIAGNOSTICS_VERSION = 0.1.0
POLIS_BOOT_DIAGNOSTICS_SITE = $(BR2_EXTERNAL_POLIS_PATH)/package/polis-boot-diagnostics/app
POLIS_BOOT_DIAGNOSTICS_SITE_METHOD = local
POLIS_BOOT_DIAGNOSTICS_DEPENDENCIES = boot-diagnostics-tools
POLIS_BOOT_DIAGNOSTICS_OVERRIDE_SRCDIR_RSYNC_EXCLUSIONS = --exclude target/

# The collector uses only the Rust standard library; no registry downloads.
define POLIS_BOOT_DIAGNOSTICS_CONFIGURE_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) $(PKG_CARGO_ENV) \
		cargo fetch --locked --offline --target $(RUSTC_TARGET_NAME)
endef

ifeq ($(BR2_TOOLCHAIN_USES_MUSL),y)
POLIS_BOOT_DIAGNOSTICS_CARGO_ENV = CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_RUSTFLAGS="-C target-feature=+crt-static"
endif

define POLIS_BOOT_DIAGNOSTICS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/target/$(RUSTC_TARGET_NAME)/$(if $(BR2_ENABLE_DEBUG),debug,release)/polis-boot-diagnostics \
		$(TARGET_DIR)/usr/bin/polis-boot-diagnostics
	$(INSTALL) -D -m 0755 $(POLIS_BOOT_DIAGNOSTICS_PKGDIR)/polis-boot-diagnostics.init \
		$(TARGET_DIR)/etc/init.d/polis-boot-diagnostics
endef

$(eval $(cargo-package))
