BUILDROOT ?= /home/broosegoose/Software/buildroot-2026.08
EXTERNAL := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
BOARD ?= polis_rpi4
BOARDS := $(patsubst %_defconfig,%,$(notdir $(wildcard $(EXTERNAL)/configs/*_defconfig)))
# Preserve the existing Pi 4 output directory.
ifeq ($(BOARD),polis_rpi4)
O ?= $(EXTERNAL)/output/rpi4
else
O ?= $(EXTERNAL)/output/$(BOARD)
endif
# Reuse the Buildroot source tree's download cache.
DL_DIR ?= $(BUILDROOT)/dl

BR_MAKE = $(MAKE) -C "$(BUILDROOT)" O="$(abspath $(O))" \
	BR2_EXTERNAL="$(EXTERNAL)" BR2_DL_DIR="$(abspath $(DL_DIR))"
FLASH_SCRIPT := $(EXTERNAL)/scripts/flash-sd.sh
IMAGE_ROOTS = "$(EXTERNAL)/output" "$(BUILDROOT)/output" "$(abspath $(O))"
export IMAGE DEVICE

.DEFAULT_GOAL := help
.PHONY: help list-boards check-board check-config configure build menuconfig savedefconfig
.PHONY: list-images list-devices flash

help:
	@echo 'make list-boards   List available board configurations'
	@echo 'make configure     Load the selected board defconfig'
	@echo 'make build         Build the configured SD-card image'
	@echo 'make menuconfig    Edit the Buildroot configuration'
	@echo 'make savedefconfig Save configuration changes in this external tree'
	@echo 'make list-images   List available SD-card images'
	@echo 'make list-devices  Show disks and their mounted partitions'
	@echo 'make flash         Select an image and SD card, then confirm the write'
	@echo 'Select BOARD=<name> (default: polis_rpi4).'
	@echo 'Override BUILDROOT=, O=, or DL_DIR= as needed.'
	@echo 'For flash, optionally set IMAGE=/path/sdcard.img DEVICE=/dev/sdX.'

list-boards:
	@$(foreach board,$(BOARDS),echo '$(board)$(if $(filter $(board),$(BOARD)), (selected))';)

check-board:
	@$(if $(filter $(BOARD),$(BOARDS)),true,echo 'Unknown BOARD. Run make list-boards.' >&2; exit 1)

configure: check-board
	$(BR_MAKE) $(BOARD)_defconfig

check-config: check-board
	@test -f "$(abspath $(O))/.config" || { echo 'Run make configure first.'; exit 1; }
	@grep -Fqx 'BR2_DEFCONFIG="$(EXTERNAL)/configs/$(BOARD)_defconfig"' "$(abspath $(O))/.config" || \
		{ echo 'Output configuration does not match BOARD. Choose its O= directory or run make configure.' >&2; exit 1; }

build menuconfig: check-config
	$(BR_MAKE) $(if $(filter build,$@),all,$@)

savedefconfig: check-config
	$(BR_MAKE) savedefconfig BR2_DEFCONFIG="$(EXTERNAL)/configs/$(BOARD)_defconfig"

list-images:
	@bash "$(FLASH_SCRIPT)" list-images $(IMAGE_ROOTS)

list-devices:
	@bash "$(FLASH_SCRIPT)" list-devices

flash:
	@bash "$(FLASH_SCRIPT)" flash $(IMAGE_ROOTS)
