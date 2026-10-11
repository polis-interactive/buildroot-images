################################################################################
#
# polis-dtmerge (host only)
#
################################################################################

HOST_POLIS_DTMERGE_VERSION = 67b2a9f0b4e10ebdfe3c9cc8e616ac5c1bfa4e69
HOST_POLIS_DTMERGE_SITE = $(call github,raspberrypi,utils,$(HOST_POLIS_DTMERGE_VERSION))
HOST_POLIS_DTMERGE_LICENSE = BSD-3-Clause
HOST_POLIS_DTMERGE_LICENSE_FILES = LICENCE
HOST_POLIS_DTMERGE_DEPENDENCIES = host-dtc

# Build only dtmerge, using Buildroot's host libfdt instead of Linux sources.
define HOST_POLIS_DTMERGE_BUILD_CMDS
	$(HOSTCC) $(HOST_CFLAGS) -I$(HOST_DIR)/include/libfdt \
		-o $(@D)/dtmerge-bin $(@D)/dtmerge/dtmerge.c $(@D)/dtmerge/dtoverlay.c \
		$(HOST_LDFLAGS) -lfdt
endef

define HOST_POLIS_DTMERGE_INSTALL_CMDS
	$(INSTALL) -D -m 0755 $(@D)/dtmerge-bin $(HOST_DIR)/bin/dtmerge
endef

$(eval $(host-generic-package))
