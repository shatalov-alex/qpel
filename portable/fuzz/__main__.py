"""Differential fuzzing: random well-typed qpel programs through every path.

Each program is generated as qpel text (programs.py) and must
  1. parse, validate and print back to the same text;
  2. come back unchanged from qpel -> CUDA -> qpel and qpel -> Metal -> qpel;
  3. give the same outputs from its CUDA on the CPU simulator and its Metal on the
     Apple GPU (floats bit for bit, subnormals read as signed zeros);
  4. with --nvidia, give the same outputs from its CUDA on GPGPU-Sim, a simulated
     NVIDIA GPU (differential/gpgpusim.py; all programs run in one container).

    python3 -m fuzz --count 30 --seed 1    # check_all runs a fixed set
    python3 -m fuzz --seed 7 --keep /tmp/f # keep failing programs
    python3 -m fuzz --nvidia               # also on GPGPU-Sim
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import traceback

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from fuzz.programs import generate  # noqa: E402


def check(text, seed, work, gpu, nvidia=None):
    """None, or (stage, error) for the first failure. nvidia: (folder, pending list) to stage the
    program's CUDA for GPGPU-Sim; its cases are appended to the list and compared after the run."""
    from qpel import compile_program
    from qpel_ir import validate
    from qpel_text import parse_program, print_program
    from frontends import roundtrip
    from differential.simulate import Case, Program
    from differential.matrix import compare, load_host
    try:
        program = parse_program(text)
        validate(program)
    except ValueError as error:
        return 'parse', str(error)
    if print_program(program) != text:
        return 'print', 'printing the parsed program gave different text'
    differences = roundtrip.reimport(program)
    for dialect, difference in differences.items():
        if difference:
            return f'qpel -> {dialect} -> qpel', difference
    saved = work/'program.qpel.json'
    saved.write_text(json.dumps(program))
    compile_program(saved, work/'build')
    generated = work/'build/package/generated'
    plan = json.loads((generated/'pipeline.json').read_text())
    (work/'sim').mkdir()
    simulated = Program(next(generated.glob('*.cu')), plan, work/'sim')
    host = load_host(generated) if gpu else None
    target = None
    if nvidia:
        import shutil
        from differential.gpgpusim import GpuProgram
        folder, pending = nvidia
        folder.mkdir(parents=True)
        source = shutil.copy(next(generated.glob('*.cu')), folder/'program.cu')  # outlives `work`
        target = GpuProgram(Path(source), plan, folder/'gpu')
    rng = np.random.default_rng(seed)
    outputs = [r['name'] for r in plan['resources'] if r['kind'] == 'output']
    for size in (1, 37, 300):
        inputs = {'a': rng.integers(-2**63, 2**63 - 1, size, dtype=np.int64, endpoint=True),
                  'f': (rng.standard_normal(size)*np.exp2(rng.integers(-30, 30, size))).astype(np.float32)}
        inputs['a'][:size // 3] = rng.integers(-300, 300, size // 3)  # small values too
        case = Case({'count': (size, 1, 1)}, inputs)
        cpu = simulated.run(case)
        if target:
            case_folder = target.work/f'c{size:03}'
            case_folder.mkdir()
            target.write_case(case, case_folder)
            pending.append((target, case_folder, cpu, outputs, size))
        if gpu:
            from differential import metal
            error = compare(cpu, metal.run_generated(host, plan, case), outputs)
            if error:
                return 'cuda (simulator) vs metal (Apple GPU)', f'{size} elements: {error}'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--count', type=int, default=30)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--cpu', action='store_true', help='skip the Apple GPU comparison')
    parser.add_argument('--keep', type=Path, help='write failing programs to this folder')
    parser.add_argument('--nvidia', action='store_true', help='also run every program on GPGPU-Sim (Docker)')
    args = parser.parse_args(argv)
    nvidia_root, pending = None, []
    if args.nvidia:
        from differential import gpgpusim
        if not gpgpusim.image_ready():
            print('no Docker or GPGPU-Sim image: --nvidia needs both')
            return 1
        problem = gpgpusim.selftest()
        if problem:
            print(f'FAIL GPGPU-Sim self-test (gpgpusim/selftest.cu):\n{problem}')
            return 1
        nvidia_root = Path(tempfile.mkdtemp(prefix='qpel-fuzz-nvidia-')).resolve()
    gpu = False
    if not args.cpu:
        try:
            from differential import metal
            gpu = metal.available()
        except ImportError:
            gpu = False
    failures = 0
    from qpel_text import parse_program, print_program
    regressions = sorted((HERE/'fuzz/regressions').glob('*.qpel'))
    programs = [(f'regression {p.stem}', 0, print_program(parse_program(p.read_text()))) for p in regressions]
    programs += [(f'seed {args.seed*100000 + i}', args.seed*100000 + i, None) for i in range(args.count)]
    failed = set()

    def fail(label, seed, text, stage, error):
        nonlocal failures
        if label in failed:
            return
        failed.add(label)
        failures += 1
        print(f'FAIL {label}: {stage}\n     {error[:600]}')
        if args.keep:
            args.keep.mkdir(parents=True, exist_ok=True)
            (args.keep/f'fuzz_{seed}.qpel').write_text(text)
    staged = {}  # GpuProgram -> (label, seed, text)
    for number, (label, seed, text) in enumerate(programs):
        text = text or generate(seed)
        before = len(pending)
        nvidia = (nvidia_root/f'p{number:04}', pending) if nvidia_root else None
        with tempfile.TemporaryDirectory(prefix='qpel-fuzz-') as work:
            try:
                result = check(text, seed, Path(work), gpu, nvidia)
            except Exception:
                result = ('crash', traceback.format_exc()[-1500:])
        if result:
            fail(label, seed, text, *result)
        for target, *_ in pending[before:]:
            staged[target] = (label, seed, text)
    if pending:
        from differential import gpgpusim
        from differential.matrix import compare
        import shutil
        try:
            gpgpusim.run_all(nvidia_root, [nvidia_root])
            for target, folder, cpu, outputs, size in pending:
                actual, error = target.outcome(folder)
                error = error or compare(cpu, actual, outputs)
                if error:
                    fail(*staged[target], 'cuda (simulator) vs cuda (GPGPU-Sim)', f'{size} elements: {error}')
        finally:
            shutil.rmtree(nvidia_root, ignore_errors=True)
    print(f'{len(programs) - failures}/{len(programs)} programs passed ({len(regressions)} regressions, {args.count} random)'
          + ('' if gpu else ' (no Apple GPU: CPU paths only)') + (' (with GPGPU-Sim)' if args.nvidia else ''))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
