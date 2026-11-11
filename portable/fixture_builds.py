"""Build every IR fixture with the offline compiler (shared by the toolchain checks)."""
import json
from pathlib import Path

from qpel import compile_program

HERE = Path(__file__).resolve().parent
FIXTURES = HERE/'tests/fixtures/qpel_ir'


def fixture_source(name):
    return FIXTURES/name/'program.qpel.json'


def fixture_program(name):
    return json.loads(fixture_source(name).read_text())


def build_fixture(name, out):
    """Build one fixture into the new directory `out`; return its package/generated path."""
    compile_program(fixture_source(name), out)
    return out/'package/generated'


def build_fixtures(work):
    """Build all fixtures under `work`; yield (fixture name, generated directory)."""
    for folder in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        yield folder.name, build_fixture(folder.name, work/folder.name)
