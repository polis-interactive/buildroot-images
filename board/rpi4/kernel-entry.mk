# Explicit profile instrumentation. The idempotent hook preserves Linux objects.
ifeq ($(BR2_POLIS_KERNEL_ENTRY),y)
define POLIS_INSTALL_KERNEL_ENTRY
	python3 $(BR2_EXTERNAL_POLIS_PATH)/board/rpi4/install-kernel-entry.py $(@D)
endef
LINUX_PRE_BUILD_HOOKS += POLIS_INSTALL_KERNEL_ENTRY
endif
