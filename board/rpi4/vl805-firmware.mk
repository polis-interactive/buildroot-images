# Pi 4 start4.elf must be able to decompress VL805 firmware when the EEPROM
# bootloader has not already initialized USB. Upstream fix: firmware#2026.
# Replace the existing full Pi 4 firmware pair only; retain the pinned DTBs
# and overlays used by the kernel. These are downloads, not target packages.
ifeq ($(BR2_POLIS_RPI4_VL805_FIRMWARE),y)
POLIS_RPI4_VL805_FIRMWARE_VERSION = 0e05e17767d62a60cc2fafaafbdc42aaead3984b
POLIS_RPI4_VL805_FIRMWARE_SITE = https://raw.githubusercontent.com/raspberrypi/firmware/$(POLIS_RPI4_VL805_FIRMWARE_VERSION)/boot
RPI_FIRMWARE_EXTRA_DOWNLOADS += \
	$(POLIS_RPI4_VL805_FIRMWARE_SITE)/start4.elf \
	$(POLIS_RPI4_VL805_FIRMWARE_SITE)/fixup4.dat
RPI_FIRMWARE_HASH_FILES += $(BR2_EXTERNAL_POLIS_PATH)/board/rpi4/vl805-firmware.hash

define POLIS_INSTALL_VL805_FIRMWARE
	$(INSTALL) -m 0644 $(RPI_FIRMWARE_DL_DIR)/start4.elf $(BINARIES_DIR)/rpi-firmware/start4.elf
	$(INSTALL) -m 0644 $(RPI_FIRMWARE_DL_DIR)/fixup4.dat $(BINARIES_DIR)/rpi-firmware/fixup4.dat
endef
RPI_FIRMWARE_POST_INSTALL_IMAGES_HOOKS += POLIS_INSTALL_VL805_FIRMWARE
endif
