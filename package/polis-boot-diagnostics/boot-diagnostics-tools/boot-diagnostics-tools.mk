################################################################################
#
# boot-diagnostics-tools
#
################################################################################

BOOT_DIAGNOSTICS_TOOLS_VERSION = 67b2a9f0b4e10ebdfe3c9cc8e616ac5c1bfa4e69
BOOT_DIAGNOSTICS_TOOLS_SITE = $(call github,raspberrypi,utils,$(BOOT_DIAGNOSTICS_TOOLS_VERSION))
BOOT_DIAGNOSTICS_TOOLS_LICENSE = BSD-3-Clause
BOOT_DIAGNOSTICS_TOOLS_LICENSE_FILES = LICENCE vcgencmd/vcgencmd.c

# Both upstream tools are standalone C programs. Build just these utilities,
# without the other tools, libraries, or services in raspberrypi/utils.
ifeq ($(BR2_TOOLCHAIN_USES_MUSL),y)
BOOT_DIAGNOSTICS_TOOLS_LDFLAGS = -static
endif

define BOOT_DIAGNOSTICS_TOOLS_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) $(BOOT_DIAGNOSTICS_TOOLS_LDFLAGS) \
		-o $(@D)/vclog-bin $(@D)/vclog/vclog.c
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) $(BOOT_DIAGNOSTICS_TOOLS_LDFLAGS) \
		-o $(@D)/vcgencmd-bin $(@D)/vcgencmd/vcgencmd.c
endef

define BOOT_DIAGNOSTICS_TOOLS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/vclog-bin $(TARGET_DIR)/usr/bin/vclog
	$(INSTALL) -D -m 0755 $(@D)/vcgencmd-bin $(TARGET_DIR)/usr/bin/vcgencmd
endef

$(eval $(generic-package))
