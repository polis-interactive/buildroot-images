# Target-only linking policy. Keep host tools and BlueZ on their normal paths.
ifeq ($(BR2_POLIS_PREFER_STATIC),y)

# IWD uses libtool: -all-static is required for fully static executables.
# Use -static for configure's compiler probes, which do not run through libtool.
IWD_CONF_ENV += \
	PKG_CONFIG="$(PKG_CONFIG_HOST_BINARY) --static" \
	LDFLAGS="$(TARGET_LDFLAGS) -static"
IWD_MAKE_OPTS += LDFLAGS="$(TARGET_LDFLAGS) -all-static"
IWD_INSTALL_TARGET_OPTS += LDFLAGS="$(TARGET_LDFLAGS) -all-static"
IWD_INSTALL_STAGING_OPTS += LDFLAGS="$(TARGET_LDFLAGS) -all-static"

# libdbus.a remains available to dynamic clients such as BlueZ. D-Bus itself
# needs no shared plugins in this profile, so its executables can be static.
DBUS_CONF_OPTS += --default-library=static -Dprefer_static=true
DBUS_LDFLAGS += -static

# Kmod normally dlopens compression libraries. Link those dependencies
# directly, and use our optional Meson setting for the kmod executable only.
KMOD_CONF_OPTS += -Ddlopen=[] -Dprefer_static=true -Dstatic-tools=true

# HOST_XZ_CONF_OPTS references XZ_CONF_OPTS lazily in Buildroot. Freeze the
# host options before adding target-only PIC and symbol-version settings.
HOST_XZ_CONF_OPTS := $(HOST_XZ_CONF_OPTS)

# liblzma.a must also work inside the shared libkmod built by upstream.
# Linux-specific symbol aliases cannot be combined with --with-pic when
# building both library types. Generic versioning uses only the linker map.
XZ_CONF_OPTS += --with-pic --enable-symbol-versions=generic
# xz, xzdec, and lzmainfo use Automake's generic LINK rule, which reads
# AM_LDFLAGS, not per-program overrides. lzmadec has its own link rule.
# liblzma uses liblzma_la_LDFLAGS, so it still builds both library types.
POLIS_XZ_LINK_OPTS = \
	AM_LDFLAGS="-all-static" \
	lzmadec_LDFLAGS="-all-static"
XZ_MAKE_OPTS += $(POLIS_XZ_LINK_OPTS)
XZ_INSTALL_TARGET_OPTS += $(POLIS_XZ_LINK_OPTS)
XZ_INSTALL_STAGING_OPTS += $(POLIS_XZ_LINK_OPTS)

endif
