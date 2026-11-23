"""NVIDIA rows beyond the corpus programs (differential/hardware.py): qpel's library pipelines and the gsplat
pilots that the test modules build, run on the GPU machine with the corpus rows.

- DeviceScan and DeviceRadixSort: qpel's CUDA against numpy and NVIDIA's own CUB, bit for bit, at every size
  and type (GPGPU-Sim runs NVIDIA's CUB right only for small 32-bit inputs; gpgpusim/README.md, Open).
- DeviceSegmentedRadixSort and DeviceMergeSort (the other sorts gsplat calls): qpel's CUDA against numpy and
  NVIDIA's CUB, the segmented sort bit for bit in its segments, the merge sort's keys bit for bit and each run
  of equal keys' values as a set (NVIDIA's merge sort is not stable).
- gsplat's tile intersection (the count and emit pipelines): qpel's CUDA against the original on the CPU
  simulator, every output bit for bit (integers only).
- gsplat's spherical harmonics (the K = 16, D = 3 kernels and the generic ones): qpel's CUDA against the
  unmodified original, both on the GPU, bit for bit. (The GPU's rsqrtf is not the simulator's, so neither is
  compared with the CPU simulator here; tests/test_gsplat_spherical_harmonics.py does that.)

prepare(root, device) builds everything under root/l*/ (build trees in sim_original/, which the bundle leaves
out) and returns [(row, label, check)]; after the GPU machine ran the cases, check() gives None or the error."""
from functools import partial
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parents[1]
for folder in (HERE, HERE/'tests'):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


def _arrays(got, want, what):
    import numpy as np
    got, want = np.asarray(got).reshape(-1), np.asarray(want).reshape(-1)
    if got.shape != want.shape:
        return f'{what}: {got.size} elements, expected {want.size}'
    differ = np.flatnonzero(got != want)
    if differ.size:
        i = int(differ[0])
        return f'{what}: {differ.size} of {want.size} differ, first at {i}: {got[i]!r}, expected {want[i]!r}'
    return None


def _cub_outcome(folder):
    status = (folder/'status').read_text().strip() if (folder/'status').exists() else 'not run'
    if status != '0':
        log = (folder/'run.log').read_text(errors='replace') if (folder/'run.log').exists() else ''
        return f'NVIDIA CUB run failed (status {status}): {log[-1500:]}'
    return None


def scan_rows(base, device):
    import numpy as np
    import test_device_scan as t
    from frontends.library import cub_scan_main
    from .gpgpusim import GpuProgram
    rows = []
    for element, exclusive, source in t.VARIANTS:
        key = f'{element}_{int(exclusive)}_{source}'
        work = base/'sim_original'/key
        work.mkdir(parents=True)
        built = t.Built(element, exclusive, source, work)
        ours = GpuProgram(built.cuda, built.plan, base/f'p_qpel_{key}')
        cub = None
        if source == element:  # (CUB sums in its input type; torch's cumsum of int32 as int64 has no CUB twin)
            cub = base/f'p_cub_{key}'
            cub.mkdir()
            (cub/'main.cu').write_text(cub_scan_main(element, exclusive))
            (cub/'nvcc.args').write_text('\n')
        for i, n in enumerate(t.SIZES):
            c, x = t.case(source, n)
            folder = ours.work/f'c{i:03}'
            folder.mkdir()
            ours.write_case(c, folder)
            theirs = None
            if cub:
                theirs = cub/f'c{i:03}'
                theirs.mkdir()
                x.tofile(theirs/'in.bin')

            def check(ours=ours, folder=folder, c=c, x=x, exclusive=exclusive, element=element, theirs=theirs):
                got, error = ours.outcome(folder, c)
                if error:
                    return error
                want = t.oracle(x, exclusive, element)
                got = np.asarray(got['out']).view(want.dtype)
                problem = _arrays(got, want, 'qpel against numpy')
                if problem or theirs is None:
                    return problem
                return _cub_outcome(theirs) or _arrays(got, np.fromfile(theirs/'out.bin', dtype=want.dtype),
                                                        "qpel against NVIDIA's CUB")
            rows.append((f'{device}: library DeviceScan', f'DeviceScan {key} n={n}', check))
    return rows


def sort_rows(base, device):
    import numpy as np
    import test_device_radix_sort as t
    from frontends.library import cub_sort_main
    from .gpgpusim import GpuProgram
    rows = []
    for key, value in t.VARIANTS:
        name = f'{key}_{value}'
        work = base/'sim_original'/name
        work.mkdir(parents=True)
        built = t.Built(key, value, work)
        ours = GpuProgram(built.cuda, built.plan, base/f'p_qpel_{name}')
        cub = base/f'p_cub_{name}'
        cub.mkdir()
        (cub/'main.cu').write_text(cub_sort_main(key, value))
        (cub/'nvcc.args').write_text('\n')
        for i, (c, keys, values, begin, end) in enumerate(t.cases(key, value)):
            folder, theirs = ours.work/f'c{i:03}', cub/f'c{i:03}'
            folder.mkdir()
            theirs.mkdir()
            ours.write_case(c, folder)
            keys.tofile(theirs/'keys.bin')
            values.tofile(theirs/'values.bin')
            np.array([begin, end], np.uint32).tofile(theirs/'bits.bin')

            def check(ours=ours, folder=folder, c=c, keys=keys, values=values, begin=begin, end=end, theirs=theirs):
                got, error = ours.outcome(folder, c)
                if error:
                    return error
                want_keys, want_values = t.oracle(keys, values, begin, end)
                got_keys = np.asarray(got['keys_out']).view(keys.dtype)
                got_values = np.asarray(got['values_out']).view(values.dtype)
                problem = _arrays(got_keys, want_keys, 'keys, qpel against numpy') or \
                    _arrays(got_values, want_values, 'values, qpel against numpy') or _cub_outcome(theirs)
                if problem:
                    return problem
                return _arrays(got_keys, np.fromfile(theirs/'keys_out.bin', dtype=keys.dtype), "keys, qpel against NVIDIA's CUB") \
                    or _arrays(got_values, np.fromfile(theirs/'values_out.bin', dtype=values.dtype),
                               "values, qpel against NVIDIA's CUB")
            rows.append((f'{device}: library DeviceRadixSort', f'SortPairs {c.label}', check))
    return rows


def segmented_rows(base, device):
    import numpy as np
    import test_device_segmented_sort as t
    from frontends.library import cub_merge_sort_main, cub_segmented_sort_main
    from .gpgpusim import GpuProgram
    rows = []
    groups = [('segmented', variant, t.segmented_built, t.segmented_cases, cub_segmented_sort_main) for variant in t.SEGMENTED]
    groups += [('merge', variant, t.merge_built, t.merge_cases, cub_merge_sort_main) for variant in t.MERGE]
    for kind, variant, make, cases, main in groups:
        name = f'{kind}_' + '_'.join(variant)
        work = base/'sim_original'/name
        work.mkdir(parents=True)
        built = make(*variant, work)
        ours = GpuProgram(built.cuda, built.plan, base/f'p_qpel_{name}')
        cub = base/f'p_cub_{name}'
        cub.mkdir()
        (cub/'main.cu').write_text(main(*variant))
        (cub/'nvcc.args').write_text('\n')
        for i, row in enumerate(cases(*variant)):
            c, keys, values = row[:3]
            folder, theirs = ours.work/f'c{i:03}', cub/f'c{i:03}'
            folder.mkdir()
            theirs.mkdir()
            ours.write_case(c, folder)
            keys.tofile(theirs/'keys.bin')
            values.tofile(theirs/'values.bin')
            if kind == 'segmented':
                c.inputs['begin_offsets'].tofile(theirs/'begin.bin')
                c.inputs['end_offsets'].tofile(theirs/'end.bin')
                np.array(row[4:6], np.uint32).tofile(theirs/'bits.bin')

            def check(folder=folder, c=c, row=row, theirs=theirs, kind=kind, ours=ours):
                got, error = ours.outcome(folder, c)
                if error:
                    return error
                keys, values = row[1], row[2]
                problem = _cub_outcome(theirs)
                if kind == 'segmented':
                    spans, begin, end = row[3:6]
                    want_keys, want_values = t.segmented_oracle(keys, values, spans, begin, end)
                    got_keys = np.asarray(got['keys_out']).view(keys.dtype)
                    got_values = np.asarray(got['values_out']).view(values.dtype)
                    problem = _arrays(got_keys, want_keys, 'keys, qpel against numpy') or \
                        _arrays(got_values, want_values, 'values, qpel against numpy') or problem
                    if problem:
                        return problem
                    inside = t.written(keys.size, spans)
                    their_keys = np.fromfile(theirs/'keys_out.bin', dtype=keys.dtype)
                    their_values = np.fromfile(theirs/'values_out.bin', dtype=values.dtype)
                    return _arrays(got_keys[inside], their_keys[inside], "keys in segments, qpel against NVIDIA's CUB") \
                        or _arrays(got_values[inside].view(np.uint32), their_values[inside].view(np.uint32),
                                   "value bits in segments, qpel against NVIDIA's CUB")
                want_keys, want_values = t.merge_oracle(keys, values)
                got_keys = np.asarray(got['keys']).view(keys.dtype)
                got_values = np.asarray(got['values']).view(values.dtype)
                problem = _arrays(got_keys, want_keys, 'keys, qpel against numpy') or \
                    _arrays(got_values, want_values, 'values, qpel against numpy') or problem
                if problem:
                    return problem
                theirs_pairs = t.same_pairs(got_keys, got_values, np.fromfile(theirs/'keys_out.bin', dtype=keys.dtype),
                                            np.fromfile(theirs/'values_out.bin', dtype=values.dtype))
                return theirs_pairs and f"qpel against NVIDIA's merge sort: {theirs_pairs}"
            library = 'DeviceSegmentedRadixSort' if kind == 'segmented' else 'DeviceMergeSort'
            rows.append((f'{device}: library {library}', f'SortPairs {c.label}', check))
    return rows


def intersect_rows(base, device):
    import numpy as np
    import test_gsplat_intersect as t
    from .gpgpusim import GpuProgram
    pipelines = {}
    for name in ('intersect_tile_count', 'intersect_tile_emit'):
        work = base/'sim_original'/name
        work.mkdir(parents=True)
        pipelines[name] = t.Pipeline(name, work)
    rows = []
    scenes = t.cases(pipelines['intersect_tile_count'], pipelines['intersect_tile_emit'])
    for name, index in (('intersect_tile_count', 1), ('intersect_tile_emit', 2)):
        pipeline = pipelines[name]
        ours = GpuProgram(pipeline.cuda, pipeline.plan, base/f'p_qpel_{name}')
        for i, row in enumerate(scenes):
            case = row[index]
            if case is None:
                continue
            folder = ours.work/f'c{i:03}'
            folder.mkdir()
            ours.write_case(case, folder)
            want = pipeline.original.run(case)

            def check(ours=ours, folder=folder, case=case, want=want, outputs=pipeline.outputs):
                got, error = ours.outcome(folder, case)
                if error:
                    return error
                for output in outputs:
                    problem = _arrays(np.asarray(got[output]).reshape(-1).view(want[output].dtype), want[output],
                                      f'{output}, qpel against the original (CPU simulator)')
                    if problem:
                        return problem
                return None
            rows.append((f'{device}: gsplat {name}', f'IntersectTile.cu {row[0]}', check))
    return rows


def _kernel_rows(base, device, name, built, cases, outputs):
    """qpel's CUDA and the unmodified original of one built kernel, on the GPU, bit for bit with each other."""
    from .gpgpusim import GpuProgram
    include = built.original.include
    ours = GpuProgram(built.generated/(built.plan['module'] + '.cu'), built.plan, base/f'p_qpel_{name}')
    original = GpuProgram(built.source, built.plan, base/f'p_original_{name}', fmad=False, original=built.report,
                          include=include)
    rows = []
    for i, (case, keep) in enumerate(cases):
        a, b = ours.work/f'c{i:03}', original.work/f'c{i:03}'
        for program, folder in ((ours, a), (original, b)):
            folder.mkdir()
            program.write_case(case, folder)

        def check(case=case, a=a, b=b, keep=keep):
            import numpy as np
            got, error = ours.outcome(a, case)
            want, other = original.outcome(b, case)
            if error or other:
                return error or other
            for output in outputs:
                x, y = (np.asarray(v[output]).reshape(-1)[:keep.size].view(np.uint32)[keep] for v in (got, want))
                problem = _arrays(x, y, f'{output} bits, qpel against the original (both on the GPU)')
                if problem:
                    return problem
            return None
        rows.append((f'{device}: gsplat {name}', f'{built.source.name} {case.label}', check))
    return rows


def harmonics_rows(base, device):
    import numpy as np
    import test_gsplat_spherical_harmonics as k16
    import test_gsplat_sh_generic as generic
    rows = []
    for coefficients, degree in k16.VARIANTS:
        work = base/'sim_original'/f'k16_{coefficients}_{degree}'
        work.mkdir(parents=True)
        built = k16.Built(coefficients, degree, work)
        cases = []
        for case in k16.cases(coefficients):
            elements = case.shapes['E'][0]
            kept = case.inputs['masks'].astype(bool) if 'masks' not in case.omit else np.ones(elements, bool)
            cases.append((case, np.repeat(kept, 3)))  # (a masked element's colours are not written)
        rows += _kernel_rows(base, device, f'sh_k16_{coefficients}_{degree}', built, cases, ['colors'])
    for key, (source, kernel, skip) in generic.KERNELS.items():
        work = base/'sim_original'/f'sh_{key}'
        work.mkdir(parents=True)
        built = generic.Built(source, kernel, work)
        cases = [(case, np.repeat(generic.written(case), case.values['D'])) for case in generic.cases(skip)]
        rows += _kernel_rows(base, device, f'sh_{key}', built, cases, ['colors'])
    return rows


GROUPS = (('library_scan', scan_rows), ('library_sort', sort_rows), ('library_segmented', segmented_rows),
          ('gsplat_intersect', intersect_rows),
          ('gsplat_harmonics', harmonics_rows))


def prepare(root, device, names=None):
    """[(row, label, check)] of every group (or those named), built under root/l<k>/."""
    rows = []
    for index, (group, make) in enumerate(GROUPS):
        if names and group not in names:
            continue
        base = Path(root)/f'l{index:03}'
        base.mkdir()
        try:
            rows += make(base, device)
        except (ValueError, RuntimeError, ImportError) as error:
            print(f'extra rows: could not prepare {group}: {error}', file=sys.stderr, flush=True)
            rows.append((f'{device}: {group}', group, partial(lambda e: f'could not prepare: {e}', error)))
    return rows
