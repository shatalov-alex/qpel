"""Build every IR fixture with the offline compiler (shared by the toolchain checks)."""
import json
from pathlib import Path
import tempfile

from qpel_ir import export
from qpel import compile_program

HERE = Path(__file__).resolve().parent
FIXTURES = HERE/'tests/fixtures/qpel_ir'


def fixture_program(name):
    folder = FIXTURES/name
    return export(*(json.loads((folder/f).read_text()) for f in ('module.json', 'pipeline.json')))


def build_fixture(name, out):
    """Build one fixture into the new directory `out`; return its package/generated path."""
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp)/'program.qpel.json'
        source.write_text(json.dumps(fixture_program(name)))
        compile_program(source, out)
    return out/'package/generated'


def build_fixtures(work):
    """Build all fixtures under `work`; yield (fixture name, generated directory)."""
    for folder in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        yield folder.name, build_fixture(folder.name, work/folder.name)
