"""qpel's Triton frontend from the command line (frontends/triton/README.md).

    python3 -m frontends.triton kernel.ttir [--num-warps 4] [--out kernel.qpel]   # TTIR -> qpel text
    python3 -m frontends.triton --fixtures       # corpus/triton/build -> tests/fixtures/qpel_ir/triton_*
"""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE))
from frontends.triton.lower import lower  # noqa: E402

CORPUS = HERE/'corpus/triton'
FIXTURES = HERE/'tests/fixtures/qpel_ir'


def corpus_kernels():
    return list(json.loads((CORPUS/'kernels.json').read_text()))


def corpus_program(name):
    """The qpel IR of corpus kernel `name`, from its committed TTIR and launch metadata."""
    from qpel_text import parse_program
    meta = json.loads((CORPUS/'build'/f'{name}.json').read_text())
    text, _ = lower((CORPUS/'build'/f'{name}.ttir').read_text(), meta['num_warps'], 'triton_' + name)
    return parse_program(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('ttir', nargs='?', help='a TTIR file (one tt.func)')
    parser.add_argument('--num-warps', type=int, default=4, help='the num_warps it was compiled with (default 4)')
    parser.add_argument('--module', help='the qpel module name (default: the function name)')
    parser.add_argument('--out', help='write the qpel text here (default: print it)')
    parser.add_argument('--fixtures', action='store_true', help='rewrite the fixtures of corpus/triton')
    args = parser.parse_args(argv)
    if args.fixtures:
        for name in corpus_kernels():
            folder = FIXTURES/f'triton_{name}'
            folder.mkdir(exist_ok=True)
            (folder/'program.qpel.json').write_text(json.dumps(corpus_program(name), indent=2) + '\n')
            print('wrote', folder.relative_to(HERE))
        return 0
    if not args.ttir:
        parser.error('give a TTIR file, or --fixtures')
    text, lowering = lower(Path(args.ttir).read_text(), args.num_warps, args.module)
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
