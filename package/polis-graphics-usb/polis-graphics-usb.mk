POLIS_GRAPHICS_USB_VERSION = 1
POLIS_GRAPHICS_USB_SITE = $(BR2_EXTERNAL_POLIS_PATH)/package/polis-graphics-usb/src
POLIS_GRAPHICS_USB_SITE_METHOD = local
POLIS_GRAPHICS_USB_DEPENDENCIES = host-python3
POLIS_GRAPHICS_USB_PAYLOAD = $(BR2_EXTERNAL_POLIS_PATH)/.local/graphics-usb

define POLIS_GRAPHICS_USB_BUILD_CMDS
	$(HOST_DIR)/bin/python3 $(POLIS_GRAPHICS_USB_PKGDIR)/prepare.py --check
	$(TARGET_CC) $(TARGET_CFLAGS) -Wall -Wextra -Werror $(TARGET_LDFLAGS) \
		-o $(@D)/polis-graphics-usb $(@D)/graphics-usb.c
endef

define POLIS_GRAPHICS_USB_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/polis-graphics-usb $(TARGET_DIR)/usr/sbin/polis-graphics-usb
	$(INSTALL) -D -m 0755 $(POLIS_GRAPHICS_USB_PAYLOAD)/hdmi-graphics-hello \
		$(TARGET_DIR)/usr/bin/hdmi-graphics-hello
	$(INSTALL) -D -m 0644 $(POLIS_GRAPHICS_USB_PAYLOAD)/screen-controller-graphics.toml \
		$(TARGET_DIR)/usr/share/polis-graphics-usb/screen-controller-graphics.toml
	$(INSTALL) -D -m 0644 $(POLIS_GRAPHICS_USB_PAYLOAD)/manifest.json \
		$(TARGET_DIR)/usr/share/polis-graphics-usb/manifest.json
endef

define POLIS_GRAPHICS_USB_INSTALL_INIT_SYSV
	$(INSTALL) -D -m 0755 $(POLIS_GRAPHICS_USB_PKGDIR)/S09polis-graphics-usb \
		$(TARGET_DIR)/etc/init.d/S09polis-graphics-usb
endef

$(eval $(generic-package))
