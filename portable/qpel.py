"""Check or build a saved qpel program without the MPS IDE or GPU libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from qpel_ir import validate, Lowering
from build_backends import build


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def compile_program(source, out=None):
    raw = source.read_bytes()
    program = json.loads(raw)
    checks = {target: validate(program, target) for target in ('metal', 'cuda')}
    report = {'status': 'checked', 'source_sha256': hashlib.sha256(raw).hexdigest(),
              'semantics': program['semantics'], 'checks': checks,
              'gpu_tested': False, 'cuda_compiled': False, 'metal_compiled': False}
    if out is None:
        return report
    if out.exists() or out.is_symlink():
        raise ValueError('Output already exists; choose a new directory')
    out.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation prevents accidental replacement, including concurrent builds.
    out.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix='.qpel-', dir=out.parent) as temp:
            stage = Path(temp)
            (stage/'program.qpel.json').write_bytes(raw)
            neutral = Lowering(program).write(stage/'neutral')
            manifest = build(neutral, stage/'generated')
            # Record final provenance paths rather than deleted staging paths.
            for key in ('neutral_source', 'neutral_pipeline'):
                manifest[key] = str(out.resolve()/'package'/Path(manifest[key]).relative_to(stage.resolve()))
            save(stage/'generated/manifest.json', manifest)
            report['status'] = 'generated'
            report['artifact_sha256'] = {
                str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(stage.rglob('*')) if p.is_file()}
            save(stage/'build-report.json', report)
            # Publish a complete payload inside the reserved output directory.
            stage.rename(out/'package')
    except Exception:
        # Remove only our empty reservation. Never delete an existing user's files.
        try: out.rmdir()
        except OSError: pass
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'build'))
    parser.add_argument('source', type=Path, help='Exported program.qpel.json')
    parser.add_argument('--out', type=Path, help='New output directory (build only)')
    args = parser.parse_args(argv)
    if args.command == 'build' and args.out is None: parser.error('build requires --out')
    if args.command == 'check' and args.out is not None: parser.error('check does not accept --out')
    try:
        report = compile_program(args.source, args.out)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'qpel: {error}\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
