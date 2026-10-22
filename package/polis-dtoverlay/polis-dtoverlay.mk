################################################################################
#
# polis-dtoverlay
#
################################################################################

POLIS_DTOVERLAY_VERSION = 67b2a9f0b4e10ebdfe3c9cc8e616ac5c1bfa4e69
POLIS_DTOVERLAY_SITE = $(call github,raspberrypi,utils,$(POLIS_DTOVERLAY_VERSION))
POLIS_DTOVERLAY_LICENSE = BSD-3-Clause
POLIS_DTOVERLAY_LICENSE_FILES = LICENCE
POLIS_DTOVERLAY_DEPENDENCIES = dtc

# Compile the overlay helper into the executable; do not install libdtovl,
# dtmerge, dtparam, or the other raspberrypi/utils tools and services.
define POLIS_DTOVERLAY_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) $(TARGET_LDFLAGS) \
		-o $(@D)/dtoverlay-bin $(@D)/dtmerge/dtoverlay_main.c \
		$(@D)/dtmerge/dtoverlay.c $(@D)/dtmerge/utils.c -lfdt
endef

define POLIS_DTOVERLAY_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/dtoverlay-bin $(TARGET_DIR)/usr/bin/dtoverlay
endef

$(eval $(generic-package))
