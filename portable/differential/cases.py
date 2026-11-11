"""Shared test vectors: the same cases are run through every source of one program.

Inputs respect each original kernel's own preconditions, so the original never
executes undefined behaviour (which the simulator's UBSan build would report):
scale_offset keeps (int)i * offset within int, SAM 2 needs even sizes, grid_stride's n
is at most the buffer size.
"""
import numpy as np

from .simulate import Case, DTYPES, VALUE_TYPES, volume

SHAPES = [(1, 1, 1), (5, 1, 1), (17, 3, 2), (64, 4, 1), (129, 7, 3), (33, 33, 1)]


def random_array(rng, dtype, count):
    if dtype == np.uint8:
        return rng.integers(0, 256, count, dtype=np.uint8)
    if dtype in (np.uint32, np.int32):
        info = np.iinfo(dtype)
        return rng.integers(info.min, info.max, count, dtype=dtype, endpoint=True)
    # Normal floats away from subnormals, whose handling differs between CPUs and Apple GPUs,
    # and (for half) from overflow.
    low, high = (-13, 15) if dtype == np.float16 else (-20, 20)
    magnitude = np.exp2(rng.uniform(low, high, count))
    return (np.where(rng.random(count) < 0.5, -1, 1)*magnitude).astype(dtype)


def generic(plan, seed, shapes=SHAPES, values=None):
    rng = np.random.default_rng(seed)
    grids = sorted({s['grid'] for s in plan['stages']})
    cases = []
    for shape in shapes:
        sizes = {g: shape for g in grids}
        inputs = {r['name']: random_array(rng, DTYPES[r['type']], volume(sizes[r['extent']]))
                  for r in plan['resources'] if r['kind'] == 'input'}
        chosen = {}
        for r in plan['resources']:
            if r['kind'] == 'value':
                kind = VALUE_TYPES[r['type']]
                if values and r['name'] in values:
                    chosen[r['name']] = values[r['name']]()
                else:
                    chosen[r['name']] = (int(rng.integers(0, 2**32)) if kind == 'u32' else
                                         int(rng.integers(-2**31, 2**31)) if kind == 'i32' else
                                         int(rng.integers(0, 2**63)) if kind == 'u64' else
                                         int(rng.integers(-2**63, 2**63 - 1)) if kind == 'i64' else
                                         float(np.float16(rng.uniform(-8, 8))) if kind == 'f16' else
                                         float(np.float32(rng.uniform(-8, 8))))
        cases.append(Case(sizes, inputs, chosen, f'{shape}'))
    return cases


def scale_offset(plan, seed):
    rng = np.random.default_rng(seed)
    return generic(plan, seed, values={'offset': lambda: int(rng.integers(-1000, 1001)),
                                       'scale': lambda: float(np.float32(rng.uniform(-8, 8)))})


def sam2_cc(plan, seed):
    """Masks of varied density on even sizes, as SAM 2 requires; one image per call."""
    rng = np.random.default_rng(seed)
    cases = []
    sizes = [(2, 2), (4, 6), (16, 16), (34, 8), (64, 48), (130, 66)]
    for k, (w, h) in enumerate(sizes*3):
        density = (k*29) % 101
        values = rng.integers(1, 256, w*h).astype(np.uint8)
        mask = np.where(rng.integers(0, 100, w*h) < density, values, 0).astype(np.uint8)
        cases.append(Case({'shape': (w, h, 1)}, {'img': mask}, label=f'{w}x{h} density {density}'))
    return cases


def grid_stride(plan, seed):
    """n is the number of elements to write, at most the buffers' size."""
    rng = np.random.default_rng(seed)
    cases = generic(plan, seed)
    for case in cases:
        size = volume(case.shapes['size'])
        case.values['n'] = int(rng.integers(0, size + 1))
        case.defined = {'y': case.values['n']}  # elements past n are never written
    return cases


SPECIAL = {'scale_offset': scale_offset, 'sam2_cc': sam2_cc, 'grid_stride': grid_stride}


def for_program(name, plan, seed=20261111, values=None):
    """Cases for a program. `values` constrains scalar arguments: 'volume' is the number of
    elements of the dispatch shape (a count the buffers must hold), 'W', 'H' or 'N' one of its
    sizes, and an integer is used as is."""
    cases = SPECIAL.get(name, generic)(plan, seed)
    for rule_name, rule in (values or {}).items():
        for case in cases:
            shape = case.shapes[next(iter(s['grid'] for s in plan['stages']))]
            if rule == 'volume':
                case.values[rule_name] = volume(shape)
            elif rule in ('W', 'H', 'N'):
                case.values[rule_name] = shape['WHN'.index(rule)]
            elif type(rule) is int:
                case.values[rule_name] = rule
            else:
                raise ValueError(f'unknown value rule {rule!r} for {rule_name}')
    return cases
