################################################################################
#
# polis-wifi-setupd
#
################################################################################

POLIS_WIFI_SETUPD_VERSION = 0.1.3
POLIS_WIFI_SETUPD_SITE = $(call github,polis-interactive,wifi-setupd,$(POLIS_WIFI_SETUPD_VERSION))
POLIS_WIFI_SETUPD_LICENSE = MIT
POLIS_WIFI_SETUPD_LICENSE_FILES = LICENSE-MIT
POLIS_WIFI_SETUPD_DEPENDENCIES = host-python3 iwd dbus readline
POLIS_WIFI_SETUPD_WEB_SOURCE = wifi-setupd-web-$(POLIS_WIFI_SETUPD_VERSION).tar.gz
POLIS_WIFI_SETUPD_EXTRA_DOWNLOADS = \
	https://github.com/polis-interactive/wifi-setupd/releases/download/$(POLIS_WIFI_SETUPD_VERSION)/$(POLIS_WIFI_SETUPD_WEB_SOURCE)

define POLIS_WIFI_SETUPD_CONFIGURE_CMDS
	$(HOST_DIR)/bin/python3 $(POLIS_WIFI_SETUPD_PKGDIR)/extract-web.py \
		$(POLIS_WIFI_SETUPD_DL_DIR)/$(POLIS_WIFI_SETUPD_WEB_SOURCE) $(@D)/web
endef

# Buildroot vendors the complete locked workspace but compiles only the daemon.
define POLIS_WIFI_SETUPD_BUILD_CMDS
	cd $(@D) && $(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_RUSTFLAGS="-C target-feature=+crt-static" \
		$(HOST_DIR)/bin/python3 build-support/release.py daemon --offline \
		--cargo $(HOST_DIR)/bin/cargo --target $(RUSTC_TARGET_NAME) \
		--target-dir $(@D)/daemon-target
endef

define POLIS_WIFI_SETUPD_INSTALL_TARGET_CMDS
	$(HOST_DIR)/bin/python3 $(@D)/build-support/release.py install \
		--binary $(@D)/daemon-target/$(RUSTC_TARGET_NAME)/release/wifi-setupd \
		--web-root $(@D)/web \
		--destdir $(TARGET_DIR)
	$(INSTALL) -D -m 0644 $(POLIS_WIFI_SETUPD_PKGDIR)/wifi-setupd.conf \
		$(TARGET_DIR)/etc/dbus-1/system.d/wifi-setupd.conf
endef

define POLIS_WIFI_SETUPD_INSTALL_INIT_SYSV
	$(INSTALL) -D -m 0755 $(POLIS_WIFI_SETUPD_PKGDIR)/S85wifi-setupd \
		$(TARGET_DIR)/etc/init.d/S85wifi-setupd
endef

$(eval $(cargo-package))
