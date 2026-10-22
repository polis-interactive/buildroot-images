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
                         set(dev.profile.groups) - {Group.HEADLESS}
                         | {Group.RPI4_VIDEO, Group.RPI4_UART3, Group.FFMPEG_LIBRARIES,
                            Group.RPI4_USB_HOST, Group.SCREEN_GRAPHICS_USB})
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
        self.assertEqual(workflow.output_for(self.ctx, dev),
                            workflow.output_for(self.ctx, screen))

    def test_screen_cannot_be_combined_with_headless_even_without_requirements(self):
        profile = load_profile(REPO / 'profiles/polis-rpi4-screen-dev.toml')
        conflicting = replace(profile, groups=(*profile.groups, Group.HEADLESS), requires=())
        with patch.object(resolver, 'load_profile', return_value=conflicting):
            with self.assertRaisesRegex(UserError, 'Conflicting capabilities'):
                resolver.resolve(self.ctx, profile.name)

    def test_screen_enables_host_usb_without_changing_other_profiles(self):
        screen = resolver.resolve(self.ctx, 'polis-rpi4-screen-dev', development=True)
        host = screen.groups[Group.RPI4_USB_HOST]['kernel']
        for symbol, expected in host.items():
            self.assertEqual(screen.values['kernel'][symbol], expected)
        for name in ('polis-rpi4-dev', 'polis-rpi4-speed'):
            plan = resolver.resolve(self.ctx, name, development=True)
            self.assertNotIn(Group.RPI4_USB_HOST, plan.groups)
            self.assertNotIn(Group.SCREEN_GRAPHICS_USB, plan.groups)
            self.assertNotEqual(plan.values['kernel'].get('CONFIG_USB_SUPPORT'), 'n')
        for symbol in ('CONFIG_BCMGENET', 'CONFIG_BRCMFMAC_SDIO',
                       'CONFIG_DRM_VC4', 'CONFIG_I2C_BRCMSTB',
                       'CONFIG_VIDEO_BCM2835_UNICAM_LEGACY', 'CONFIG_VIDEO_TC358743',
                       'CONFIG_SND_BCM2835_SOC_I2S'):
            self.assertEqual(screen.values['kernel'][symbol], 'y')

    def test_ffmpeg_is_library_only_and_audited(self):
        plan = resolver.resolve(self.ctx, 'polis-rpi4-screen-dev', development=True)
        output = self.root / 'ffmpeg-output'
        requested = resolver.write_generated(self.ctx, plan, output)
        expected = {
            'BR2_PACKAGE_FFMPEG': 'y',
            'BR2_PACKAGE_FFMPEG_DECODERS': 'h264 aac mp3 ac3',
            'BR2_PACKAGE_FFMPEG_DEMUXERS': 'mov',
            'BR2_PACKAGE_FFMPEG_PARSERS': 'h264 aac mpegaudio ac3',
            'BR2_PACKAGE_FFMPEG_PROTOCOLS': 'file',
        }
        for feature in ('GPL', 'NONFREE', 'FFMPEG', 'FFPLAY', 'FFPROBE',
                        'XCBGRAB', 'POSTPROC', 'SWSCALE', 'INDEVS', 'OUTDEVS'):
            expected[f'BR2_PACKAGE_FFMPEG_{feature}'] = 'n'
        for feature in ('ENCODERS', 'MUXERS', 'BSFS', 'FILTERS'):
            expected[f'BR2_PACKAGE_FFMPEG_{feature}'] = ''
        for key, value in expected.items():
            with self.subTest(symbol=key):
                self.assertEqual(requested[key], value)
                # A silently dropped codec or enabled CLI must fail the audit.
                changed = 'y' if value == 'n' else 'n'
                with self.assertRaisesRegex(UserError, key):
                    workflow.check_values(requested, dict(requested, **{key: changed}),
                                          'Buildroot', REPO, output)
        extra = set(requested['BR2_PACKAGE_FFMPEG_EXTRACONF'].split())
        for feature in ('avcodec', 'avformat', 'avutil', 'swresample'):
            self.assertIn(f'--enable-{feature}', extra)
        for feature in ('autodetect', 'programs', 'network', 'avdevice', 'avfilter',
                        'hwaccels', 'v4l2-m2m', 'libv4l2', 'libdrm', 'zlib'):
            self.assertIn(f'--disable-{feature}', extra)
        self.assertEqual(requested['BR2_SHARED_STATIC_LIBS'], 'y')
        # The feature may select FFmpeg itself, never another target package.
        self.assertTrue(all(key.startswith('BR2_PACKAGE_FFMPEG')
                            for key in plan.groups[Group.FFMPEG_LIBRARIES]['buildroot']))

    def test_ffmpeg_is_not_selected_by_any_other_profile(self):
        for name in self.ctx.profiles:
            if name == 'polis-rpi4-screen-dev':
                continue
            with self.subTest(profile=name):
                plan = resolver.resolve(self.ctx, name, development=True)
                self.assertNotIn(Group.FFMPEG_LIBRARIES, plan.groups)
                self.assertNotEqual(plan.values['buildroot'].get('BR2_PACKAGE_FFMPEG'), 'y')

    def test_screen_rejects_usb_diagnostics(self):
        profile = load_profile(REPO / 'profiles/polis-rpi4-screen-dev.toml')
        conflicting = replace(profile, groups=(*profile.groups, Group.POLIS_RUNTIME,
                                                Group.USB_PERIPHERAL_ONLY, Group.USB_DIAGNOSTICS), requires=())
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
        self.assertIn('disable_splash=1\n', firmware)
        cmdline = (output / 'profile/cmdline.txt').read_text().split()
        self.assertEqual([arg for arg in cmdline if arg.startswith('console=')],
                         ['console=ttyAMA0,115200'])
        self.assertIn('fbcon=nodefer,logo-pos:center,logo-count:1', cmdline)
        self.assertIn('vt.global_cursor_default=0', cmdline)
        self.assertIn('consoleblank=0', cmdline)
        self.assertNotIn('quiet', cmdline)
        self.assertFalse(any(arg.startswith('loglevel=') for arg in cmdline))
        self.assertEqual(requested['BR2_POLIS_KERNEL_LOGO'], 'y')
        self.assertFalse(requested.get('BR2_LINUX_KERNEL_CUSTOM_LOGO_PATH'))
        payloads = [payload['path'] for change in plan.changes
                    for payload in change.get('payloads', [])]
        self.assertTrue(any(path.startswith('profiles/groups/rpi4-video/splash/')
                             for path in payloads))
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
        for key in ('CONFIG_FB_SIMPLE', 'CONFIG_FB_BCM2708', 'CONFIG_FB_SSD1307',
                    'CONFIG_FB_RPISENSE', 'CONFIG_FB_TFT',
                    'CONFIG_FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER',
                    'CONFIG_USB_GADGET',
                    'CONFIG_USB_DWCOTG', 'CONFIG_USB_DWC2'):
            with self.subTest(symbol=key):
                self.assertEqual(kernel[key], 'n')
                with self.assertRaisesRegex(UserError, key):
                    workflow.check_values(kernel, dict(kernel, **{key: 'y'}),
                                          'kernel', REPO, output)
        for key in ('CONFIG_USB_SUPPORT', 'CONFIG_USB', 'CONFIG_USB_ACM',
                    'CONFIG_USB_XHCI_HCD', 'CONFIG_USB_XHCI_PCI',
                    'CONFIG_PCIE_BRCMSTB', 'CONFIG_RESET_RASPBERRYPI',
                    'CONFIG_FB', 'CONFIG_FB_CORE', 'CONFIG_DRM_FBDEV_EMULATION',
                    'CONFIG_FRAMEBUFFER_CONSOLE', 'CONFIG_LOGO', 'CONFIG_LOGO_LINUX_CLUT224',
                    'CONFIG_VT', 'CONFIG_VT_CONSOLE', 'CONFIG_DRM_SIMPLEDRM',
                    'CONFIG_DRM_VC4', 'CONFIG_DRM_V3D',
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

    def test_kernel_logo_is_the_supplied_512_pixel_icon(self):
        import struct
        image = REPO / 'profiles/groups/rpi4-video/splash/polis-splash.png'
        data = image.read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', data[16:24]), (512, 512))
        # pnmtologo requires ASCII RGB data with at most 224 distinct colors.
        ppm = image.with_suffix('.ppm').read_text()
        tokens = ' '.join(line.split('#', 1)[0] for line in ppm.splitlines()).split()
        self.assertEqual(tokens[:4], ['P3', '512', '512', '255'])
        channels = list(map(int, tokens[4:]))
        self.assertEqual(len(channels), 512 * 512 * 3)
        self.assertTrue(all(0 <= value <= 255 for value in channels))
        self.assertLessEqual(len(set(zip(channels[::3], channels[1::3], channels[2::3]))), 224)

    def test_logo_preparation_precedes_recording_build_inputs(self):
        with patch('src.profiles.logo.prepare') as prepare:
            def resolve(*args, **kwargs):
                prepare.assert_called_once_with(REPO / 'profiles/groups/rpi4-video/splash/polis-splash.png')
                raise UserError('stop before build')
            with patch.object(workflow, 'resolve', side_effect=resolve):
                with self.assertRaisesRegex(UserError, 'stop before build'):
                    workflow.execute(self.ctx, 'build', 'polis-rpi4-screen-dev')
