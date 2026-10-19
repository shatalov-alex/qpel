"""Rebuild the checked-in SAM 2 package in generated/ from the components IR fixture.

generated/ is consumed by sam2_adapter.py, run_host.sh, tests/cuda.cu and the GPU
runners. It must come from the offline compiler, never from hand edits.
"""
import json
from pathlib import Path
import shutil
import tempfile

from fixture_builds import build_fixture, fixture_program

HERE = Path(__file__).resolve().parent
TARGET = HERE/'generated'
FIXTURE = 'components'


def main():
    with tempfile.TemporaryDirectory() as temp:
        package = build_fixture(FIXTURE, Path(temp)/'out').parent
        generated = package/'generated'
        # Provenance paths point into the temporary build; store them relative to generated/.
        manifest = json.loads((generated/'manifest.json').read_text())
        manifest['mps_source'] = 'components.qpel.h'
        manifest['mps_pipeline'] = 'pipeline.json'
        manifest['ir_source'] = 'program.qpel.json'
        (generated/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (generated/'program.qpel.json').write_text(json.dumps(fixture_program(FIXTURE), indent=2) + '\n')
        if TARGET.exists():
            shutil.rmtree(TARGET)
        shutil.copytree(generated, TARGET)
    print('Regenerated', TARGET.relative_to(HERE.parent))


if __name__ == '__main__':
    main()
