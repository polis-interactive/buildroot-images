"""Rust host tests and kernel patch checks; never builds the kernel or image."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / 'package/polis-splash-screen'


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{args[0]} failed ({result.returncode}):\n'
                             f'{result.stdout[-5000:]}\n{result.stderr[-5000:]}')
    return result


def patch_fixture(root):
    """Reconstruct just the preimage hunks, preserving their original offsets."""
    files = {}
    path = None
    position = None
    for line in (PACKAGE / 'kernel.patch').read_text().splitlines(keepends=True):
        if line.startswith('--- a/'):
            path = line[6:].strip()
            files[path] = []
        elif line.startswith('@@ '):
            position = int(re.match(r'@@ -(\d+)', line)[1]) - 1
        elif line.startswith((' ', '-')) and path and position is not None:
            contents = files[path]
            while len(contents) <= position:
                contents.append('\n')
            contents[position] = line[1:]
            position += 1
    for name, contents in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(''.join(contents))
    return files


class SplashScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('rustc'):
            raise unittest.SkipTest('host rustc is required')
        cls.temp = tempfile.TemporaryDirectory(prefix='polis-splash-tests-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.prepare = cls.root / 'prepare'
        run(['rustc', '--edition=2021', str(PACKAGE / 'prepare.rs'), '-o', str(cls.prepare)])

    def test_rust_render_lifecycle_and_logo_validation(self):
        for source in ('src/render.rs', 'src/lifecycle.rs', 'prepare.rs'):
            with self.subTest(source=source):
                binary = self.root / (Path(source).stem + '-tests')
                run(['rustc', '--edition=2021', '--test', str(PACKAGE / source), '-o', str(binary)])
                run([str(binary)])

    def test_buildroot_host_flags_do_not_leak_into_rustc_arguments(self):
        """Exercise the Kbuild proc-macro command shape with a tiny host crate."""
        cc = shutil.which('gcc')
        if not cc:
            self.skipTest('host gcc is required')
        work = self.root / 'host-flags'
        work.mkdir()
        (work / 'include').mkdir()
        (work / 'lib').mkdir()
        (work / 'include/marker.h').write_text('#define MARKER 1\n')
        (work / 'probe.c').write_text('#include <marker.h>\nint marker = MARKER;\n')
        (work / 'probe.rs').write_text(
            'extern crate proc_macro;\n#[proc_macro]\n'
            'pub fn passthrough(input: proc_macro::TokenStream) -> proc_macro::TokenStream { input }\n')
        (work / 'Makefile').write_text(f'''BR2_POLIS_SPLASH_SCREEN = y
BR2_EXTERNAL_POLIS_PATH = {REPO}
HOSTCC = {cc}
HOSTCC_NOCCACHE = {cc}
HOST_CFLAGS = -O2 -I$(CURDIR)/include
HOST_LDFLAGS = -L$(CURDIR)/lib -Wl,-rpath,$(CURDIR)/lib
POLIS_SPLASH_RUSTC = {shutil.which('rustc')}
POLIS_SPLASH_BINDGEN = unused-for-this-host-test
# The upstream Buildroot assignment embeds flags inside HOSTCC.
LINUX_MAKE_FLAGS = HOSTCC="$(HOSTCC) $(HOST_CFLAGS) $(HOST_LDFLAGS)"
include {PACKAGE / 'polis-splash-screen.mk'}
.PHONY: check link
check:
\t$(MAKE) --no-print-directory link $(LINUX_MAKE_FLAGS)
link:
\t@echo 'effective HOSTCC=$(HOSTCC)'
\t@echo 'effective HOSTCFLAGS=$(HOSTCFLAGS)'
\t@echo 'effective HOSTLDFLAGS=$(HOSTLDFLAGS)'
\t$(HOSTCC) $(HOSTCFLAGS) -c probe.c -o probe.o
\t$(RUSTC) --edition=2021 -Clinker-flavor=gcc -Clinker=$(HOSTCC) -Clink-args='$(HOSTLDFLAGS)' --crate-type=proc-macro --crate-name=probe probe.rs -o probe.so
''')
        result = run(['make', '--no-print-directory', 'check'], cwd=work)
        self.assertIn(f'effective HOSTCC={cc}\n', result.stdout)
        self.assertIn(f'effective HOSTCFLAGS=-O2 -isystem {work}/include\n', result.stdout)
        self.assertIn(f'effective HOSTLDFLAGS=-L{work}/lib -Wl,-rpath,{work}/lib\n', result.stdout)
        self.assertTrue((work / 'probe.so').is_file())

    def test_patch_installs_once_and_rejects_partial_changes(self):
        kernel = self.root / 'fixture'
        kernel.mkdir()
        files = patch_fixture(kernel)
        run([str(self.prepare), str(REPO), str(kernel)])
        driver = kernel / 'drivers/gpu/drm/vc4/vc4_drv.c'
        self.assertIn('ret = polis_splash_screen_setup(drm);', driver.read_text())
        header = kernel / 'rust/bindings/bindings_helper.h'
        self.assertIn('#include "../../drivers/gpu/drm/drm_internal.h"', header.read_text())
        target = kernel / 'drivers/gpu/drm/vc4/polis-splash-screen'
        self.assertEqual((target / 'polis-logo.rgb').stat().st_size, 512 * 512 * 3)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in kernel.rglob('*') if p.is_file()}
        run([str(self.prepare), str(REPO), str(kernel)])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
        # Partial application must not get "repaired" by guessing patch direction.
        header.write_text(''.join(files['rust/bindings/bindings_helper.h']))
        before = {p: p.read_bytes() for p in before}
        result = subprocess.run([str(self.prepare), str(REPO), str(kernel)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('partially applied', result.stderr)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    @unittest.skipUnless(os.environ.get('POLIS_SPLASH_TEST_KERNEL'),
                         'optional real-header check: set POLIS_SPLASH_TEST_KERNEL')
    def test_real_pinned_bindings_typecheck(self):
        """C ABI/type check with Rust API shims: explicitly not a kernel build."""
        kernel = Path(os.environ['POLIS_SPLASH_TEST_KERNEL']).resolve()
        bindgen = os.environ.get('POLIS_SPLASH_BINDGEN') or shutil.which('bindgen')
        if not bindgen:
            bindgen = str(REPO / '.local/polis-splash-host/bin/bindgen')
        work = self.root / 'real-headers'
        work.mkdir()
        # Copy only patched files; all C includes come from the pinned tree.
        files = patch_fixture(work)
        for name in files:
            shutil.copyfile(kernel / name, work / name)
        internal = work / 'drivers/gpu/drm/drm_internal.h'
        shutil.copyfile(kernel / 'drivers/gpu/drm/drm_internal.h', internal)
        run([str(self.prepare), str(REPO), str(work)])
        generated = work / 'bindings.rs'
        args = [bindgen, str(work / 'rust/bindings/bindings_helper.h'),
                '--rust-target', '1.68', '--use-core', '--with-derive-default',
                '--ctypes-prefix', 'core::ffi', '--no-layout-tests', '--no-debug', '.*',
                '--enable-function-attribute-detection', '-o', str(generated)]
        for line in (kernel / 'rust/bindgen_parameters').read_text().splitlines():
            if line and not line.startswith('#'):
                args.extend(line.split())
        args += ['--', '-w', '--target=aarch64-linux-gnu', '-nostdinc', '-std=gnu11',
                 '-fno-builtin', '-D__KERNEL__', '-D__BINDGEN__', '-DMODULE',
                 '-DCONFIG_DRM_POLIS_SPLASH_SCREEN=1', '-DKBUILD_MODNAME="polis_splash_screen"']
        for path in ('arch/arm64/include', 'arch/arm64/include/generated', 'include',
                     'arch/arm64/include/uapi', 'arch/arm64/include/generated/uapi',
                     'include/uapi', 'include/generated/uapi'):
            args += ['-I' + str(kernel / path)]
        for path in ('include/linux/compiler-version.h', 'include/linux/kconfig.h',
                     'include/linux/compiler_types.h'):
            args += ['-include', str(kernel / path)]
        run(args, cwd=kernel, env=dict(os.environ, RUST_LOG='error'))
        shim = work / 'libkernel.rlib'
        run(['rustc', '--edition=2021', '--crate-name', 'kernel', '--crate-type', 'rlib',
             str(PACKAGE / 'tests/kernel_shim.rs'), '-o', str(shim)],
            env=dict(os.environ, POLIS_TEST_BINDINGS=str(generated)))
        source = work / 'drivers/gpu/drm/vc4/polis-splash-screen/polis_splash_screen.rs'
        run(['rustc', '--edition=2021', '--crate-type', 'rlib', '--emit=metadata',
             '-Dunsafe_op_in_unsafe_fn', '-Wmissing_docs', '-Wrust_2018_idioms',
             '-Wunreachable_pub', '-Dwarnings', str(source),
             '--extern', 'kernel=' + str(shim), '-o', str(work / 'splash.rmeta')])


if __name__ == '__main__':
    unittest.main()
