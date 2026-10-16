"""The shipped screen profile preserves dev access and rejects headless composition."""
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src.common import Context, UserError
from src.profiles.loader import load_profile
from src.profiles.options import Group
from src.profiles import resolver, workflow

REPO = Path(__file__).resolve().parents[1]


class ScreenDevelopmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        br = self.root / 'buildroot'
        (br / 'configs').mkdir(parents=True)
        (br / 'Makefile').write_text('BR2_VERSION_EPOCH = 1788535000\n')
        shutil.copyfile(REPO / 'profiles/base/upstream_defconfig',
                        br / 'configs/raspberrypi4_64_defconfig')
        self.ctx = Context(REPO, br, br / 'dl', self.root / 'snapshots')
        self.enterContext(patch.object(resolver, 'input_manifest', return_value=[]))

    def test_screen_reuses_all_development_features_except_headless(self):
        dev = resolver.resolve(self.ctx, 'polis-rpi4-dev', development=True)
        screen = resolver.resolve(self.ctx, 'polis-rpi4-screen-dev', development=True)
        self.assertEqual(set(screen.profile.groups),
                         set(dev.profile.groups) - {Group.HEADLESS} | {Group.RPI4_VIDEO})
        self.assertTrue(set(dev.profile.requires) <= set(screen.profile.requires))
        for group in (Group.WIFI_IWD, Group.WIFI_SETUPD, Group.DEVELOPMENT_ACCESS):
            for domain in ('buildroot', 'kernel', 'busybox'):
                for key, value in screen.groups[group.value].get(domain, {}).items():
                    self.assertEqual(screen.values[domain][key], value)
        self.assertEqual(screen.values['buildroot']['BR2_SHARED_STATIC_LIBS'], 'y')
        self.assertEqual(screen.values['buildroot']['BR2_POLIS_PREFER_STATIC'], 'y')
        self.assertEqual(screen.values['busybox']['CONFIG_STATIC'], 'y')
        # Dev's no-getty behavior survives removing the headless kernel group.
        self.assertEqual(screen.values['rootfs']['etc/inittab'],
                         dev.values['rootfs']['etc/inittab'])
        self.assertNotEqual(workflow.output_for(self.ctx, dev),
                            workflow.output_for(self.ctx, screen))

    def test_screen_cannot_be_combined_with_headless_even_without_requirements(self):
        profile = load_profile(REPO / 'profiles/polis-rpi4-screen-dev.toml')
        conflicting = replace(profile, groups=(*profile.groups, Group.HEADLESS), requires=())
        with patch.object(resolver, 'load_profile', return_value=conflicting):
            with self.assertRaisesRegex(UserError, 'Conflicting capabilities'):
                resolver.resolve(self.ctx, profile.name)

    def test_generated_overlays_and_required_selections_fail_closed(self):
        plan = resolver.resolve(self.ctx, 'polis-rpi4-screen-dev', development=True)
        output = self.root / 'output'
        requested = resolver.write_generated(self.ctx, plan, output)
        firmware = (output / 'profile/config.txt').read_text()
        self.assertIn('dtoverlay=vc4-kms-v3d,cma-256\n', firmware)
        self.assertIn('dtoverlay=tc358743,media-controller\n', firmware)
        self.assertIn('dtoverlay=tc358743-audio\n', firmware)
        self.assertLess(firmware.index('dtoverlay=tc358743,media-controller\n'),
                        firmware.index('dtoverlay=tc358743-audio\n'))
        self.assertNotIn('noaudio', firmware)
        self.assertNotIn('dtparam=audio=on', firmware)
        self.assertNotIn('max_framebuffers=0', firmware)
        for key in ('BR2_PACKAGE_WESTON', 'BR2_PACKAGE_WAYLAND', 'BR2_PACKAGE_XORG7',
                    'BR2_PACKAGE_GSTREAMER1', 'BR2_PACKAGE_KMSCUBE_GSTREAMER',
                    'BR2_PACKAGE_LIBCAMERA'):
            self.assertEqual(requested[key], 'n')
        for key in ('BR2_PACKAGE_MESA3D_GALLIUM_DRIVER_V3D',
                    'BR2_PACKAGE_MESA3D_VULKAN_DRIVER_BROADCOM',
                    'BR2_PACKAGE_MESA3D_OPENGL_EGL', 'BR2_PACKAGE_MESA3D_OPENGL_ES',
                    'BR2_PACKAGE_MESA3D_GBM', 'BR2_PACKAGE_LIBV4L_UTILS',
                    'BR2_PACKAGE_LIBDRM_INSTALL_TESTS', 'BR2_PACKAGE_KMSCUBE',
                    'BR2_PACKAGE_ALSA_LIB', 'BR2_PACKAGE_ALSA_LIB_PCM',
                    'BR2_PACKAGE_ALSA_UTILS', 'BR2_PACKAGE_ALSA_UTILS_APLAY',
                    'BR2_PACKAGE_VULKAN_TOOLS'):
            with self.subTest(symbol=key):
                self.assertEqual(requested[key], 'y')
                with self.assertRaisesRegex(UserError, key):
                    workflow.check_values(requested, dict(requested, **{key: 'n'}),
                                          'Buildroot', REPO, output)
        kernel = plan.values['kernel']
        for key in ('CONFIG_DRM_VC4', 'CONFIG_DRM_V3D',
                    'CONFIG_VIDEO_BCM2835_UNICAM_LEGACY', 'CONFIG_VIDEO_TC358743',
                    'CONFIG_MEDIA_CONTROLLER', 'CONFIG_VIDEO_V4L2_SUBDEV_API',
                    'CONFIG_SND_BCM2835_SOC_I2S', 'CONFIG_SND_SIMPLE_CARD',
                    'CONFIG_SND_SOC_SPDIF', 'CONFIG_SND_SOC_HDMI_CODEC',
                    'CONFIG_SND_PCM', 'CONFIG_SND_SOC_GENERIC_DMAENGINE_PCM',
                    'CONFIG_DMA_BCM2835', 'CONFIG_I2C_BRCMSTB',
                    'CONFIG_DMA_SHARED_BUFFER', 'CONFIG_VIDEOBUF2_DMA_CONTIG'):
            with self.subTest(symbol=key):
                self.assertEqual(kernel[key], 'y')
                with self.assertRaisesRegex(UserError, key):
                    workflow.check_values(kernel, dict(kernel, **{key: 'n'}),
                                          'kernel', REPO, output)
