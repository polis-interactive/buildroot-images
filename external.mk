include $(BR2_EXTERNAL_POLIS_PATH)/package/polis-boot-diagnostics/boot-diagnostics-tools/boot-diagnostics-tools.mk
include $(sort $(wildcard $(BR2_EXTERNAL_POLIS_PATH)/package/*/*.mk))

# Build policy and board instrumentation are not standalone packages.
include $(BR2_EXTERNAL_POLIS_PATH)/build-support/static-core/static-core.mk
include $(BR2_EXTERNAL_POLIS_PATH)/board/rpi4/kernel-entry.mk
include $(BR2_EXTERNAL_POLIS_PATH)/board/rpi4/kernel-logo.mk
include $(BR2_EXTERNAL_POLIS_PATH)/board/rpi4/vl805-firmware.mk
