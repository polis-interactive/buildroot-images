################################################################################
#
# polis-gpio-test
#
################################################################################

POLIS_GPIO_TEST_VERSION = 0.1.0
POLIS_GPIO_TEST_SITE = $(BR2_EXTERNAL_POLIS_PATH)/package/polis-gpio-test/app
POLIS_GPIO_TEST_SITE_METHOD = local
POLIS_GPIO_TEST_DEPENDENCIES = polis-firmware-tools
POLIS_GPIO_TEST_OVERRIDE_SRCDIR_RSYNC_EXCLUSIONS = --exclude target/

# Local sources bypass Cargo's archive vendoring step. Populate the shared
# Cargo cache explicitly; compilation remains locked and offline.
define POLIS_GPIO_TEST_CONFIGURE_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) $(PKG_CARGO_ENV) \
		cargo fetch --locked --target $(RUSTC_TARGET_NAME)
endef

ifeq ($(BR2_TOOLCHAIN_USES_MUSL),y)
POLIS_GPIO_TEST_CARGO_ENV = CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_RUSTFLAGS="-C target-feature=+crt-static"
endif

define POLIS_GPIO_TEST_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/target/$(RUSTC_TARGET_NAME)/$(if $(BR2_ENABLE_DEBUG),debug,release)/polis-gpio-test \
		$(TARGET_DIR)/usr/bin/polis-gpio-test
	$(INSTALL) -D -m 0755 $(POLIS_GPIO_TEST_PKGDIR)/polis-gpio-test.init \
		$(TARGET_DIR)/etc/init.d/polis-gpio-test
endef

$(eval $(cargo-package))
