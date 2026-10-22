# The checked-in PPM is already in the format consumed by pnmtologo.
ifeq ($(BR2_POLIS_KERNEL_LOGO),y)
define POLIS_INSTALL_KERNEL_LOGO
	$(INSTALL) -m 0644 $(BR2_EXTERNAL_POLIS_PATH)/profiles/groups/rpi4-video/splash/polis-splash.ppm $(@D)/drivers/video/logo/logo_linux_clut224.ppm
endef
LINUX_PRE_BUILD_HOOKS += POLIS_INSTALL_KERNEL_LOGO
endif
