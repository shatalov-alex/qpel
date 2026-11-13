"""Shared test vectors: the same cases are run through every source of one program.

Inputs respect each original kernel's own preconditions, so the original never
executes undefined behaviour (which the simulator's UBSan build would report):
scale_offset keeps (int)i * offset within int, SAM 2 needs even sizes, grid_stride's n
is at most the buffer size.
"""
import numpy as np

from .simulate import Case, DTYPES, VALUE_TYPES, volume
from qpel_schema import group_count, is_group_count

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


def random_scalar(rng, t):
    """A value of scalar type t for a struct field: integers small enough to be sizes or strides."""
    if t in ('f32', 'f16'):
        return float(np.float16(rng.uniform(-8, 8)))
    if t == 'bf16':
        return int(rng.integers(0, 2**16))
    return int(rng.integers(0, 64))


def generic(plan, seed, shapes=SHAPES, values=None):
    rng = np.random.default_rng(seed)
    grids = sorted({s['grid'] for s in plan['stages']})
    # A shape that is no dispatch's grid sizes other buffers (a table, a second tensor): it
    # gets a size of its own, the next shape in the list, so that sizes differ.
    others = sorted({r['name'] for r in plan['resources'] if r['kind'] == 'shape'} - set(grids))
    cases = []
    for i, shape in enumerate(shapes):
        sizes = {g: shape for g in grids}
        sizes.update({o: tuple(shapes[(i + 1 + j) % len(shapes)]) for j, o in enumerate(others)})
        inputs = {r['name']: random_array(rng, DTYPES[r['type']], volume(sizes[r['extent']]))
                  for r in plan['resources'] if r['kind'] in ('input', 'inout')}
        chosen = {}
        for r in plan['resources']:
            if r['kind'] == 'value' and r['type'].startswith('struct '):
                chosen[r['name']] = {f: random_scalar(rng, t) for f, t in plan['structs'][r['type'][7:]]}
                continue
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


def murmur_slot(keys, size, bits):
    """TRELLIS.2's hash (hash.cuh): the Murmur3 finalizer of a 32- or 64-bit key, modulo the size."""
    k = keys.astype(np.uint64)
    mask = np.uint64(2**bits - 1)
    if bits == 32:
        steps = ((16, 0x85ebca6b), (13, 0xc2b2ae35), (16, None))
    else:
        steps = ((33, 0xff51afd7ed558ccd), (33, 0xc4ceb9fe1a85ec53), (33, None))
    with np.errstate(over='ignore'):
        for shift, multiplier in steps:
            k ^= k >> np.uint64(shift)
            if multiplier is not None:
                k = (k * np.uint64(multiplier)) & mask
    return k % np.uint64(size)


def hashmap_cases(plan, seed):
    """TRELLIS.2's hash table kernels: a table of N slots (hashmap_keys, hashmap_values) and M
    keys, one thread per key. Empty slots hold the key type's maximum. Insertion gets distinct
    keys that hash to distinct slots, so the table it builds is the same in any thread order;
    lookups get a table built here by inserting keys one at a time (collisions included, one
    slot always left empty), and look up keys of which about half are in it."""
    rng = np.random.default_rng(seed)
    types = {r['name']: DTYPES[r['type']] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    key_type, value_type = types['hashmap_keys'], types['hashmap_values']
    bits = 8 * np.dtype(key_type).itemsize
    empty = np.iinfo(key_type).max
    inserting = any(r['name'] == 'hashmap_keys' and r['kind'] == 'inout' for r in plan['resources'])
    extents = {r['name']: r['extent'] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    table_shape, keys_shape = extents['hashmap_keys'], extents['keys']
    cases = []
    # (slots, keys): a full table, a table twice the keys (TRELLIS.2 allocates 2x), one with few keys
    for slots, count in [(1, 1), (2, 2), (11, 5), (64, 64), (515, 257), (2000, 1000), (1000, 7)]:
        if table_shape == keys_shape and slots != count:
            continue  # a plan with one size for both
        values = rng.integers(0, np.iinfo(value_type).max, count, dtype=value_type, endpoint=True)
        if inserting:
            chosen, used = [], set()
            while len(chosen) < count:
                draw = rng.integers(0, empty, 4 * count + 16, dtype=key_type)  # never the empty marker
                for key, slot in zip(draw, murmur_slot(draw, slots, bits)):
                    if len(chosen) < count and int(slot) not in used and key not in chosen:
                        chosen.append(key)
                        used.add(int(slot))
            keys = np.array(chosen, key_type)[rng.permutation(count)]  # threads insert in no particular order
            inputs = {'hashmap_keys': np.full(slots, empty, key_type), 'hashmap_values': np.zeros(slots, value_type),
                      'keys': keys, 'values': values}
        else:
            table = np.full(slots, empty, key_type)
            stored = np.zeros(slots, value_type)
            present = rng.integers(0, empty, max(slots - 1, 0), dtype=key_type)
            for key, value in zip(np.unique(present), rng.integers(0, np.iinfo(value_type).max, slots, dtype=value_type)):
                slot = int(murmur_slot(np.array([key], key_type), slots, bits)[0])
                while table[slot] != empty:
                    slot = (slot + 1) % slots
                table[slot], stored[slot] = key, value
            probes = np.where(rng.random(count) < 0.5, rng.choice(np.append(present, empty - 1), count),
                              rng.integers(0, empty, count, dtype=key_type)).astype(key_type)
            probes[probes == empty] = empty - 1
            inputs = {'hashmap_keys': table, 'hashmap_values': stored, 'keys': probes}
        cases.append(Case({table_shape: (slots, 1, 1), keys_shape: (count, 1, 1)}, inputs, {},
                          f'{slots} slots, {count} keys'))
    return cases


SPECIAL = {'scale_offset': scale_offset, 'sam2_cc': sam2_cc, 'grid_stride': grid_stride}
PREFIXES = {'trellis2_hashmap_': hashmap_cases}  # families of corpus programs


def for_program(name, plan, seed=20261111, values=None, shapes=None, inputs=None):
    """Cases for a program. `values` constrains scalar arguments: 'volume' is the number of
    elements of the dispatch shape (a count the buffers must hold), an integer is used as is,
    and any other string is an expression of the shape's W, H and N ('W/4', 'W*H').
    'args.ne0' names a field of a struct argument."""
    if shapes:  # a corpus program's own sizes (for example whole rows of a byte buffer)
        cases = generic(plan, seed, shapes=[tuple(s) for s in shapes])
    else:
        family = next((f for prefix, f in PREFIXES.items() if name.startswith(prefix)), None)
        cases = SPECIAL.get(name, family or generic)(plan, seed)
    rng = np.random.default_rng(seed + 1)
    for buffer_name, element in (inputs or {}).items():  # byte buffers holding values of another type
        dtype = DTYPES[element]
        for case in cases:
            raw = case.inputs[buffer_name]
            count = raw.size*raw.itemsize // np.dtype(dtype).itemsize
            case.inputs[buffer_name] = random_array(rng, dtype, count).view(raw.dtype)
    for rule_name, rule in (values or {}).items():
        for case in cases:
            shape = case.shapes[next(iter(s['grid'] for s in plan['stages']))]
            target, field = rule_name.split('.') if '.' in rule_name else (rule_name, None)
            def put(v):
                if field:
                    case.values[target][field] = v
                else:
                    case.values[target] = v
            if rule == 'volume':
                put(volume(shape))
            elif type(rule) is int:
                put(rule)
            elif isinstance(rule, str) and is_group_count(rule):  # an expression of W, H and N
                put(group_count(rule, shape))
            else:
                raise ValueError(f'unknown value rule {rule!r} for {rule_name}')
    return cases
