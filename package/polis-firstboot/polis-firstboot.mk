################################################################################
#
# polis-firstboot
#
################################################################################

POLIS_FIRSTBOOT_VERSION = ded1e9332bd996f6adc46e10d22d9ef0298b1882
POLIS_FIRSTBOOT_SITE = https://raw.githubusercontent.com/raspberrypi/rpi-eeprom/$(POLIS_FIRSTBOOT_VERSION)
POLIS_FIRSTBOOT_SOURCE = rpi-eeprom-config
POLIS_FIRSTBOOT_EXTRA_DOWNLOADS = \
	$(POLIS_FIRSTBOOT_SITE)/firmware-2711/old/stable/pieeprom-2025-11-05.bin \
	$(POLIS_FIRSTBOOT_SITE)/LICENSE
POLIS_FIRSTBOOT_LICENSE = BSD-3-Clause
POLIS_FIRSTBOOT_LICENSE_FILES = LICENSE
POLIS_FIRSTBOOT_DEPENDENCIES = python3 boot-diagnostics-tools

define POLIS_FIRSTBOOT_EXTRACT_CMDS
	cp $(POLIS_FIRSTBOOT_DL_DIR)/rpi-eeprom-config $(@D)/
	cp $(POLIS_FIRSTBOOT_DL_DIR)/pieeprom-2025-11-05.bin $(@D)/
	cp $(POLIS_FIRSTBOOT_DL_DIR)/LICENSE $(@D)/
endef

define POLIS_FIRSTBOOT_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/rpi-eeprom-config $(TARGET_DIR)/usr/share/polis-firstboot/rpi-eeprom-config
	$(INSTALL) -D -m 0644 $(@D)/pieeprom-2025-11-05.bin $(TARGET_DIR)/usr/share/polis-firstboot/pieeprom.bin
endef

$(eval $(generic-package))
