"""qpel verify: does a port behave like its original?

    qpel verify original.cu port.metal [--kernel NAME ...] [--map ORIGINAL=PORT ...] [-I DIR] [--json FILE]

The original is the reference. It is imported to qpel, which gives the launch plan (its
kernels, resources and how each argument is fed), the cases and the numeric tolerance. The port
is only read for its kernels' signatures: it need not import to qpel, so a hand-written or
AI-generated port with constructs qpel does not cover can still be checked. Each port kernel
(same name, or --map) takes the original's arguments in the same order, builtins such as
[[thread_position_in_grid]] aside, and the element types must match.

Both sides then run the same cases (the behaviour matrix's, plus copies whose input buffers
mix in edge values: NaN, infinities, signed zeros, subnormals, type limits), each where it runs here: CUDA on the CPU simulator (the
CUDA semantics qpel's behaviour rows use), Metal on the Apple GPU. Outputs are compared under
NUMERICS.md as in the behaviour matrix: bit-exact, or within twice the stated ulp bound where
the original calls bounded math functions; +0 and -0 count as equal when either side is Metal
(Apple's GPUs flush zero signs). The first difference of each failing case is reported.

Exit status: 0 when every case agrees, 1 when one differs, 2 when the port cannot be run.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from frontends import clang_ast  # noqa: E402
from frontends.c_import import Importer, describe, import_source, params_of  # noqa: E402
from qpel_ir import Lowering  # noqa: E402
from qpel_types import EXACT_MATH, MATH_ULPS  # noqa: E402

from . import cases as test_cases  # noqa: E402


class PortError(ValueError):
    """The port cannot be run against the original (a missing kernel, a signature that does not map)."""


def element(qual_type):
    """The element or scalar type of a parameter, for comparing the two signatures."""
    kind = describe(qual_type)
    return kind[2] if kind[0] == 'pointer' else kind[1] if kind[0] == 'scalar' else kind[-1]


def port_report(path, original, plan, include=(), mapping=None):
    """An import report for the port, made from its signatures alone: for every kernel of the
    plan, the port kernel (same name or mapping[name]) and how each of its arguments is fed,
    taken position by position from the original's report."""
    path = Path(path)
    dialect = clang_ast.dialect_of(path)
    unit, source = clang_ast.parse(path, dialect, include)
    importer = Importer(unit, source, path, dialect, original['module'])
    by_name = {d['name']: d for d in importer.decls}
    arguments, qualified = {}, {}
    for kernel in dict.fromkeys(s['kernel'] for s in plan['stages']):
        name = (mapping or {}).get(kernel, kernel)
        decl = by_name.get(name)
        if decl is None or not importer.dialect.is_kernel(decl):
            kernels = sorted(d['name'] for d in importer.decls if importer.dialect.is_kernel(d))
            raise PortError(f'{path.name} has no kernel {name} (kernels: {", ".join(kernels) or "none"}); '
                            f'use --map {kernel}=PORT_KERNEL')
        params = [p for p in params_of(decl) if not importer.dialect.builtin(p)]
        wanted = original['arguments'][kernel]
        if len(params) != len(wanted):
            raise PortError(f'{name} takes {len(params)} arguments ({", ".join(p["name"] for p in params)}); '
                            f'the original {kernel} takes {len(wanted)} ({", ".join(a["name"] for a in wanted)})')
        mapped = []
        for position, (param, theirs) in enumerate(zip(params, wanted)):
            ours = element(param['type']['qualType'])
            if ours != theirs['type']:
                raise PortError(f'{name} argument {position} ({param["name"]}) is {ours}; the original\'s '
                                f'({theirs["name"]}) is {theirs["type"]}')
            mapped.append(dict(theirs, name=param['name'], binding=importer.dialect.binding(param, position)))
        arguments[kernel] = mapped
        qualified[kernel] = importer.qualified.get(name, name)
    return {'source': str(path), 'dialect': dialect, 'module': original['module'], 'kernels': list(arguments),
            'qualified': qualified, 'arguments': arguments, 'host_spans': importer.host_spans,
            'header_host_bodies': importer.header_host_bodies}


class Side:
    """One source and the way it runs here."""

    def __init__(self, path, plan, report, include, work, constants=None):
        self.path, self.plan, self.report, self.include, self.constants = Path(path), plan, report, include, constants
        self.dialect = report['dialect']
        if self.dialect == 'cuda':
            from .simulate import Program
            Path(work).mkdir(parents=True)
            self.program = Program(self.path, plan, work, original=report, include=include)
        else:
            from . import metal
            if not metal.available():
                raise PortError('running Metal needs PyTorch with an Apple GPU (MPS)')

    @property
    def where(self):
        return 'CPU simulator (CUDA)' if self.dialect == 'cuda' else 'Apple GPU (Metal)'

    def run(self, case):
        if self.dialect == 'cuda':
            return self.program.run(case)
        from . import metal
        return metal.run_original(self.path, self.plan, self.report, case, self.include, self.constants)


def verify(original, port, kernels=None, mapping=None, include=(), constants=None, edges=True):
    """{'cases': [...], 'passed': bool, ...}: the port against the original on every case."""
    from .compare import compare
    original, port = Path(original), Path(port)
    program, report = import_source(original, include=include, kernels=kernels, constants=constants)
    plan = Lowering(program).plan()
    theirs = port_report(port, report, plan, include, mapping)
    outputs = [r['name'] for r in plan['resources'] if r['kind'] in ('output', 'inout')]
    bounded = any(n['kind'] == 'Math' and n['attributes']['function'] not in EXACT_MATH for n in program['nodes'])
    ulps = 2 * MATH_ULPS if bounded else 0
    zero_sign = report['dialect'] == 'cuda' and theirs['dialect'] == 'cuda'
    result = {'original': str(original), 'port': str(port), 'module': report['module'],
              'kernels': {k: theirs['qualified'][k] for k in theirs['kernels']}, 'ulps': ulps, 'cases': []}
    with tempfile.TemporaryDirectory(prefix='qpel-verify-') as work:
        work = Path(work)
        reference = Side(original, plan, report, include, work/'original', constants)
        candidate = Side(port, plan, theirs, include, work/'port', constants)
        result['runs'] = {'original': reference.where, 'port': candidate.where}
        found = test_cases.for_program(report['module'], plan)
        # No subnormal inputs where Metal runs: Apple GPUs flush them (NUMERICS.md), a platform difference.
        found += test_cases.with_edges(found, plan, 20261111, subnormals=zero_sign) if edges else []
        for case in found:
            entry = {'case': case.label}
            try:
                expected = reference.run(case)
            except RuntimeError as error:
                if case.label.endswith('edge values'):  # such inputs are outside what the original accepts
                    entry['skipped'] = f'the original fails on these inputs: {str(error)[:300]}'
                    result['cases'].append(entry)
                    continue
                entry['error'] = f'the original failed: {error}'
            else:
                try:
                    actual = candidate.run(case)
                except RuntimeError as error:
                    entry['error'] = f'the port failed: {error}'
                else:
                    difference = compare(expected, actual, outputs, case.defined, ulps, None, zero_sign)
                    if difference:
                        entry['difference'] = difference.replace(', qpel ', ', port ')
            result['cases'].append(entry)
    result['passed'] = not any('error' in c or 'difference' in c for c in result['cases'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='qpel verify', description=__doc__.split('\n\n')[0])
    parser.add_argument('original', type=Path, help='the reference: a CUDA (.cu) or Metal (.metal) source')
    parser.add_argument('port', type=Path, help='the port to check against it')
    parser.add_argument('--kernel', action='append', dest='kernels', metavar='NAME',
                        help='verify only this kernel of the original (repeatable)')
    parser.add_argument('--map', action='append', default=[], metavar='ORIGINAL=PORT',
                        help='the port names a kernel differently (repeatable)')
    parser.add_argument('-I', action='append', default=[], dest='include', type=Path, metavar='DIR',
                        help='include directory for both sources (repeatable)')
    parser.add_argument('--json', type=Path, metavar='FILE', help='write the machine-readable report here')
    parser.add_argument('--no-edges', action='store_true',
                        help='leave out the cases with edge values (NaN, infinities, signed zeros, subnormals, limits)')
    args = parser.parse_args(argv)
    mapping = {}
    for item in args.map:
        if '=' not in item:
            parser.error(f'--map {item}: expected ORIGINAL=PORT')
        a, b = item.split('=', 1)
        mapping[a] = b
    try:
        result = verify(args.original, args.port, args.kernels, mapping, args.include, edges=not args.no_edges)
    except (PortError, ValueError, RuntimeError) as error:
        result = {'original': str(args.original), 'port': str(args.port), 'passed': False, 'error': str(error)}
        print(f'qpel verify: {error}', file=sys.stderr)
        if args.json:
            args.json.write_text(json.dumps(result, indent=1) + '\n')
        return 2
    for c in result['cases']:
        problem = c.get('error') or c.get('difference')
        status = 'FAIL' if problem else 'SKIP' if 'skipped' in c else 'PASS'
        print(f'{status} {c["case"]}' + (f'\n     {problem or c["skipped"]}' if problem or 'skipped' in c else ''))
    failed = sum(1 for c in result['cases'] if 'error' in c or 'difference' in c)
    skipped = sum(1 for c in result['cases'] if 'skipped' in c)
    kernels = ', '.join(f'{k} -> {v}' if k != v else k for k, v in result['kernels'].items())
    print(f'{args.port.name} against {args.original.name} ({kernels}): '
          f'{len(result["cases"]) - failed - skipped}/{len(result["cases"]) - skipped} cases agree'
          + (f' ({skipped} edge cases skipped: the original rejects them)' if skipped else '')
          + f' [{result["runs"]["original"]} vs {result["runs"]["port"]}'
          + (f', within {result["ulps"]} ulp' if result['ulps'] else ', bit-exact') + ']')
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + '\n')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
