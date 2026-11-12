"""Declared features against what the build enables, for every IR fixture.

A program's features come from its IR (Lowering.features()). The build must record exactly
those in the launch plan and manifest, enable exactly their support blocks in both generated
sources (FEATURE_DEFINES), and ask for the Metal language version they need. Every feature
must have a support block in both backends (except where NOT_GATED says why not) and be used
by at least one fixture, so no backend support goes untested.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import build_backends  # noqa: E402
from fixture_builds import FIXTURES, build_fixture, fixture_program  # noqa: E402
from qpel_ir import Lowering  # noqa: E402

NAMES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())
# Features a backend needs no support for, so it never tests their define.
NOT_GATED = {('metal.h', 'QPEL_F16_ENABLED'): 'half is a Metal built-in; CUDA needs <cuda_fp16.h>'}
DEFINE = re.compile(r'#define (QPEL_\w+_ENABLED) 1\n')


def leading_defines(text):
    """The #define block a generated source starts with (the build writes it first)."""
    found = []
    for line in text.splitlines(keepends=True):
        match = DEFINE.fullmatch(line)
        if not match:
            break
        found.append(match.group(1))
    return found


class Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix='qpel-features-')
        root = Path(cls.work.name)
        with ThreadPoolExecutor() as pool:
            cls.generated = dict(zip(NAMES, pool.map(lambda n: build_fixture(n, root/n), NAMES)))

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def test_every_feature_has_support_in_both_backends(self):
        for feature, define in build_backends.FEATURE_DEFINES:
            for backend in ('cuda.h', 'metal.h'):
                text = (HERE/'backends'/backend).read_text()
                used = re.search(rf'#if(def)?\s+{define}\b', text) is not None
                self.assertEqual(used, (backend, define) not in NOT_GATED, f'{define} in backends/{backend}')

    def test_every_feature_is_used_by_a_fixture(self):
        used = set()
        for name in NAMES:
            used |= set(json.loads((self.generated[name]/'pipeline.json').read_text())['features'])
        for feature, define in build_backends.FEATURE_DEFINES:
            wanted = set(feature) if isinstance(feature, tuple) else {feature}
            self.assertTrue(wanted & used, f'no fixture uses {feature} ({define})')


def fixture_test(name):
    def test(self):
        generated = self.generated[name]
        declared = sorted(Lowering(fixture_program(name)).features())
        plan = json.loads((generated/'pipeline.json').read_text())
        manifest = json.loads((generated/'manifest.json').read_text())
        self.assertEqual(sorted(plan['features']), declared, 'launch plan')
        self.assertEqual(sorted(manifest['features']), declared, 'manifest')
        expected = DEFINE.findall(build_backends.defines(declared).decode())
        for suffix in ('.cu', '.metal'):
            source = next(generated.glob('*' + suffix))
            self.assertEqual(leading_defines(source.read_text()), expected, source.name)
        standards = sorted({build_backends.METAL_STANDARD[f] for f in declared if f in build_backends.METAL_STANDARD})
        build = manifest.get('outputs', {}).get('metal', {})
        if standards:
            self.assertEqual(build.get('standard'), standards[-1], 'Metal language version')
    test.__doc__ = f'features of {name}'
    return test


for _name in NAMES:
    setattr(Features, f'test_{_name}', fixture_test(_name))


if __name__ == '__main__':
    unittest.main(verbosity=2)
