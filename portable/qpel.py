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


def import_program(source, plan=None, module=None, block=64):
    """Import a CUDA (.cu) or Metal (.metal) source; return (program, report)."""
    from frontends.c_import import import_source
    from qpel_schema import is_identifier, is_block_size, MAX_BLOCK_SIZE
    if module is not None and not is_identifier(module):
        raise ValueError('Invalid module name')
    if not is_block_size(block):
        raise ValueError(f'Block size must be 1..{MAX_BLOCK_SIZE}')
    plan_data = json.loads(plan.read_text()) if plan else None
    program, report = import_source(source, module=module, plan=plan_data, block_size=block)
    report['checks'] = {target: validate(program, target) for target in ('metal', 'cuda')}
    report['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    return program, report


def translate(source, out, plan=None, module=None, block=64):
    """Import a source and build the CUDA and Metal package from it."""
    program, report = import_program(source, plan, module, block)
    with tempfile.TemporaryDirectory(prefix='.qpel-import-') as temp:
        saved = Path(temp)/'program.qpel.json'
        save(saved, program)
        build_report = compile_program(saved, out)
    save(out/'package/import-report.json', report)
    (out/'package'/('original' + source.suffix)).write_bytes(source.read_bytes())
    return dict(build_report, imported=report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('check', help='Validate a program.qpel.json')
    check.add_argument('source', type=Path)
    build = commands.add_parser('build', help='Build CUDA and Metal from a program.qpel.json')
    build.add_argument('source', type=Path)
    build.add_argument('--out', type=Path, required=True, help='New output directory')
    for name, text in (('import', 'Import a CUDA or Metal source into qpel IR'),
                       ('translate', 'Import a CUDA or Metal source and build both backends')):
        command = commands.add_parser(name, help=text)
        command.add_argument('source', type=Path, help='.cu or .metal file')
        command.add_argument('--out', type=Path, required=True,
                             help='program.qpel.json to write' if name == 'import' else 'New output directory')
        command.add_argument('--plan', type=Path, help='Launch plan (pipeline.json); default: one dispatch per kernel')
        command.add_argument('--module', help='Module name; default: the plan module or the file name')
        command.add_argument('--block', type=int, default=64, help='Block size when there is no plan')
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            report = compile_program(args.source)
        elif args.command == 'build':
            report = compile_program(args.source, args.out)
        elif args.command == 'import':
            if args.out.exists():
                raise ValueError('Output already exists; choose a new file')
            program, report = import_program(args.source, args.plan, args.module, args.block)
            save(args.out, program)
        else:
            report = translate(args.source, args.out, args.plan, args.module, args.block)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'qpel: {error}\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
