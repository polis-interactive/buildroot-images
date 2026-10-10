# Pi 4 Polis images only; keep the stock baseline untouched. An idempotent
# source hook allows adding instrumentation without discarding kernel objects.
ifneq ($(filter %/board/polis/rpi4/linux.fragment,$(call qstrip,$(BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES))),)
define POLIS_INSTALL_KERNEL_ENTRY
	python3 $(BR2_EXTERNAL_POLIS_PATH)/board/polis/rpi4/install-kernel-entry.py $(@D)
endef
LINUX_PRE_BUILD_HOOKS += POLIS_INSTALL_KERNEL_ENTRY
endif
