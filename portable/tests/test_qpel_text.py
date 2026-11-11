"""The text syntax: every fixture prints, parses back to the same program, and prints the same again."""
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from frontends import roundtrip  # noqa: E402
from qpel_ir import validate  # noqa: E402
from qpel_text import ParseError, parse_program, print_program  # noqa: E402


class TextSyntax(unittest.TestCase):
    def test_every_fixture_round_trips_through_text(self):
        for folder in sorted(p for p in roundtrip.FIXTURES.iterdir() if p.is_dir()):
            with self.subTest(fixture=folder.name):
                program = json.loads((folder/'program.qpel.json').read_text())
                text = print_program(program)
                parsed = parse_program(text)
                validate(parsed)
                self.assertIsNone(roundtrip.difference(program, parsed))
                self.assertEqual(print_program(parsed), text)

    @unittest.skipUnless(__import__('shutil').which('clang++'), 'clang++ required')
    def test_a_program_written_as_text_builds_and_runs(self):
        import tempfile
        import numpy as np
        from qpel import compile_program
        from differential.simulate import Case, Program
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            compile_program(HERE/'examples/saxpy.qpel', work/'out')
            generated = work/'out/package/generated'
            plan = json.loads((generated/'pipeline.json').read_text())
            (work/'sim').mkdir()
            program = Program(generated/'saxpy.cu', plan, work/'sim')
            rng = np.random.default_rng(7)
            for size, n in ((1, 1), (300, 300), (1000, 999)):
                x, y = (rng.standard_normal(size).astype(np.float32) for _ in range(2))
                out = program.run(Case({'size': (size, 1, 1)}, {'x': x, 'y': y}, {'a': 1.5, 'n': n}))['z']
                expected = (np.float32(1.5)*x + y).astype(np.float32)
                np.testing.assert_array_equal(out[:n].view(np.uint32), expected[:n].view(np.uint32))

    def test_errors_name_the_line(self):
        text = print_program(roundtrip.fixture('threshold'))
        broken = text.replace('binary[', 'missing[', 1)
        with self.assertRaisesRegex(ParseError, r'line \d+: unknown name missing'):
            parse_program(broken)


if __name__ == '__main__':
    unittest.main(verbosity=2)
