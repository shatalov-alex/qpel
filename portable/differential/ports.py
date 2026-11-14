"""Ports written outside qpel, verified against their originals (corpus/ports.json).

Each entry names an original kernel, a port of it that qpel did not write (by hand, or by an
AI model), and the qpel verify options that connect them (--map, --arg, --constant). This
runs qpel verify on every entry and prints one line each; --json writes the full reports.

    python3 -m differential.ports [--kind hand|ai] [--only TEXT] [--json FILE] [--nvidia] [--metal WHERE]

An entry with "metal": "preflight" (ports with loops that may not end, such as hash-table
probes) runs on the CPU simulator first and on the Apple GPU only if every case finished there.
"""
import argparse
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from . import verify as verifier  # noqa: E402

CORPUS = HERE/'corpus'
LIST = CORPUS/'ports.json'


def entries(kind=None, only=None):
    for entry in json.loads(LIST.read_text())['ports']:
        if (kind is None or entry['kind'] == kind) and (only is None or only in entry['kernel'] or only in entry['port']):
            yield entry


def resolve(path):
    """corpus-relative, or relative to portable/ (the AI ports live in ports/)."""
    for base in (CORPUS, HERE):
        if (base/path).exists():
            return base/path
    raise FileNotFoundError(path)


def check(entry, nvidia=False, metal=None):
    """qpel verify's result for one entry (or {'error': ...} when the port cannot be run)."""
    try:
        return verifier.verify(resolve(entry['original']), resolve(entry['port']), kernels=[entry['kernel']],
                               mapping={entry['kernel']: entry['port_kernel']}, constants=entry.get('constants'),
                               arguments=entry.get('arguments'), values=entry.get('values'), nvidia=nvidia,
                               dispatch=entry.get('dispatch', 'threads'), metal=metal or entry.get('metal', 'gpu'))
    except (verifier.PortError, ValueError, RuntimeError) as error:
        return {'passed': False, 'error': str(error), 'cases': []}


def line(entry, result, seconds):
    cases = result['cases']
    agree = sum(1 for c in cases if not ({'error', 'difference', 'skipped'} & set(c)))
    counted = sum(1 for c in cases if 'skipped' not in c)
    status = 'PASS' if result['passed'] else 'FAIL'
    detail = result.get('error') or next((c.get('difference') or c.get('error') for c in cases
                                          if 'difference' in c or 'error' in c), '')
    tolerance = '' if 'ulps' not in result else (f'within {result["ulps"]} ulp' if result['ulps'] else 'bit-exact')
    text = (f'{status} {entry["kind"]:<4} {entry["kernel"]} -> {entry["port_kernel"]}: {agree}/{counted} cases'
            f' {tolerance} ({seconds:.0f} s)')
    return text + (f'\n     {detail[:400]}' if detail else '')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m differential.ports', description=__doc__.split('\n\n')[0])
    parser.add_argument('--kind', choices=('hand', 'ai'))
    parser.add_argument('--only', metavar='TEXT', help='entries whose kernel or port contains TEXT')
    parser.add_argument('--json', type=Path, metavar='FILE')
    parser.add_argument('--nvidia', action='store_true', help='run CUDA on GPGPU-Sim')
    parser.add_argument('--metal', choices=('gpu', 'cpu', 'preflight'),
                        help="where Metal runs, for every entry (default: each entry's own, else the GPU)")
    args = parser.parse_args(argv)
    reports, passed = [], 0
    for entry in entries(args.kind, args.only):
        start = time.monotonic()
        result = check(entry, args.nvidia, args.metal)
        print(line(entry, result, time.monotonic() - start), flush=True)
        passed += result['passed']
        reports.append({'entry': entry, 'result': result})
    print(f'\n{passed}/{len(reports)} ports agree with their originals')
    if args.json:
        args.json.write_text(json.dumps(reports, indent=1) + '\n')
    return 0 if passed == len(reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
