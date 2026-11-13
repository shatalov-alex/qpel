"""Shared test vectors: the same cases are run through every source of one program.

Inputs respect each original kernel's own preconditions, so the original never
executes undefined behaviour (which the simulator's UBSan build would report):
scale_offset keeps (int)i * offset within int, SAM 2 needs even sizes, grid_stride's n
is at most the buffer size.
"""
import numpy as np

from .simulate import Case, DTYPES, VALUE_TYPES, volume
from qpel_schema import group_count, is_group_count
from qpel_types import buffer_length

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
        inputs = {r['name']: random_array(rng, DTYPES[r['type']], buffer_length(r, sizes))
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


def hash3d_cases(plan, seed):
    """TRELLIS.2's hash tables of voxel coordinates: M coordinates (b, x, y, z) as an [M, 4] int32
    tensor, keyed by b*W*H*D + x*H*D + y*D + z in a table of N slots. Insertion (values given, or
    the coordinate's index with idx_as_val) gets coordinates whose keys go to distinct slots, so
    the table is the same in any thread order. Lookups get a table built here one key at a time
    and coordinates of which about half are in it, some outside the volume (answered with the
    value type's maximum)."""
    rng = np.random.default_rng(seed)
    resources = {r['name']: r for r in plan['resources']}
    keys_name = 'hashmap_keys' if 'hashmap_keys' in resources else 'hashmap'
    values_name = 'hashmap_values' if 'hashmap_values' in resources else 'values'
    key_type, value_type = DTYPES[resources[keys_name]['type']], DTYPES[resources[values_name]['type']]
    bits = 8 * np.dtype(key_type).itemsize
    empty = np.iinfo(key_type).max
    inserting = resources[keys_name]['kind'] == 'inout'
    table_shape, coords_shape = resources[keys_name]['extent'], resources['coords']['extent']
    cases = []
    for slots, count, (w, h, d) in [(1, 1, (1, 1, 1)), (11, 5, (4, 3, 2)), (64, 64, (8, 8, 8)),
                                    (515, 257, (16, 9, 7)), (2000, 1000, (32, 32, 32))]:
        def key(c):
            return (int(c[0]) * w * h * d + int(c[1]) * h * d + int(c[2]) * d + int(c[3])) % 2**bits

        def coordinate():
            return np.array([rng.integers(0, 3), rng.integers(0, w), rng.integers(0, h), rng.integers(0, d)], np.int32)
        values = {'W': w, 'H': h, 'D': d}
        if inserting:
            chosen, used, seen = [], set(), set()
            for _ in range(200 * count + 100):
                if len(chosen) == count:
                    break
                c = coordinate()
                k = key(c)
                slot = int(murmur_slot(np.array([k], key_type), slots, bits)[0])
                if k not in seen and slot not in used:
                    chosen.append(c)
                    seen.add(k)
                    used.add(slot)
            count = len(chosen)  # a small volume may hold fewer distinct keys than asked for
            coords = np.stack(chosen)[rng.permutation(count)]
            inputs = {keys_name: np.full(slots, empty, key_type), values_name: np.zeros(slots, value_type),
                      'coords': coords.reshape(-1)}
            if 'values' in resources and values_name != 'values':
                inputs['values'] = rng.integers(0, np.iinfo(value_type).max, count, dtype=value_type, endpoint=True)
        else:
            table = np.full(slots, empty, key_type)
            stored = np.zeros(slots, value_type)
            present = [coordinate() for _ in range(max(slots - 1, 0))]
            for c in present:
                k = key(c)
                slot = int(murmur_slot(np.array([k], key_type), slots, bits)[0])
                while table[slot] != empty and table[slot] != k:
                    slot = (slot + 1) % slots
                table[slot], stored[slot] = k, rng.integers(0, np.iinfo(value_type).max, dtype=value_type)
            probes = []
            for i in range(count):
                c = present[rng.integers(0, len(present))].copy() if present and rng.random() < 0.5 else coordinate()
                if rng.random() < 0.1:  # outside the volume on one axis
                    axis = int(rng.integers(1, 4))
                    c[axis] = -1 if rng.random() < 0.5 else (w, h, d)[axis - 1]
                probes.append(c)
            inputs = {keys_name: table, values_name: stored, 'coords': np.stack(probes).reshape(-1)}
        cases.append(Case({table_shape: (slots, 1, 1), coords_shape: (count, 1, 1)}, inputs, values,
                          f'{slots} slots, {count} coordinates in {w}x{h}x{d}'))
    return cases


SPECIAL = {'scale_offset': scale_offset, 'sam2_cc': sam2_cc, 'grid_stride': grid_stride}
PREFIXES = {'trellis2_hashmap_': hashmap_cases, 'trellis2_hash3d_': hash3d_cases}  # families of corpus programs


RASTER_IMAGES = [(16, 16), (37, 23), (64, 48)]  # (W, H); TRELLIS.2 tiles are 8 x 8 (config.h)
TILE = 8


def camera(rng):
    """Column-major 4x4 view and projection matrices, as TRELLIS.2 passes them: a camera at the
    origin looking down +z, slightly rotated, with a perspective projection (w = z)."""
    a, b = rng.uniform(-0.2, 0.2, 2)
    rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    view = np.eye(4)
    view[:3, :3] = rx @ ry
    view[:3, 3] = rng.uniform(-0.3, 0.3, 3)
    fx, fy = rng.uniform(0.8, 1.6, 2)
    proj = np.array([[fx, 0, 0, 0], [0, fy, 0, 0], [0, 0, 1.0, -0.1], [0, 0, 1.0, 0]]) @ view
    # matrix[0], matrix[4], matrix[8], matrix[12] is the first row: column-major storage
    return view.T.reshape(-1).astype(np.float32), proj.T.reshape(-1).astype(np.float32)


def raster_rect(box, grid):
    """getRect (auxiliary.h): the tiles a pixel box (x0, y0, x1, y1) touches, clamped to the grid."""
    gx, gy = grid
    lo = (min(gx, max(0, int(box[0] / TILE))), min(gy, max(0, int(box[1] / TILE))))
    hi = (min(gx, max(0, int((box[2] + TILE - 1) / TILE))), min(gy, max(0, int((box[3] + TILE - 1) / TILE))))
    return lo, hi


def raster_cases(plan, seed):
    """TRELLIS.2's voxel rasteriser stages, each with inputs the previous stage would give it.
    preprocess: voxel centres in front of (and some behind) a random camera; outputs start at
    zero, as the host's torch::zeros. duplicateWithKeys: boxes in the image (some empty), their
    tile counts' inclusive sum as offsets, and key/value lists of exactly that total.
    identifyTileRanges: a sorted list of (tile << 32 | depth bits) keys and zeroed ranges."""
    rng = np.random.default_rng(seed)
    names = {r['name'] for r in plan['resources']}
    cases = []
    for (w, h), count in zip(RASTER_IMAGES * 2, [1, 7, 40, 300, 64, 129]):
        grid = (-(-w // TILE), -(-h // TILE), 1)
        if 'positions' in names and 'point_list' not in names:  # preprocess
            view, proj = camera(rng)
            depth = rng.uniform(-0.5, 6.0, count)
            spread = rng.uniform(-1.2, 1.2, (count, 2)) * np.maximum(depth, 0.3)[:, None]
            positions = np.column_stack([spread, depth]).astype(np.float32)
            inputs = {'positions': positions.reshape(-1), 'viewmatrix': view, 'projmatrix': proj,
                      'bboxes': np.zeros(4 * count, np.int32), 'depths': np.zeros(count, np.float32),
                      'tiles_touched': np.zeros(count, np.uint32)}
            values = {'voxel_size': float(np.float32(rng.uniform(0.02, 0.4))), 'width': w, 'height': h}
            cases.append(Case({'num_nodes': (count, 1, 1), 'grid': grid}, inputs, values, f'{count} voxels, {w}x{h}'))
        elif 'offsets' in names:  # duplicateWithKeys
            boxes = np.zeros((count, 4), np.int32)
            tiles = np.zeros(count, np.int64)
            for i in range(count):
                if rng.random() < 0.8:
                    x0, y0 = int(rng.integers(0, w)), int(rng.integers(0, h))
                    box = (x0, y0, int(rng.integers(x0 + 1, w + 1)), int(rng.integers(y0 + 1, h + 1)))
                    (lx, ly), (hx, hy) = raster_rect(box, grid[:2])
                    if (hx - lx) * (hy - ly):
                        boxes[i], tiles[i] = box, (hx - lx) * (hy - ly)
            if not tiles.any():
                boxes[0], tiles[0] = (0, 0, 1, 1), 1
            offsets = np.cumsum(tiles).astype(np.int64)
            rendered = int(offsets[-1])
            inputs = {'depths': rng.uniform(0.2, 9.0, count).astype(np.float32), 'offsets': offsets, 'bboxes': boxes.reshape(-1),
                      'keys_unsorted': np.zeros(rendered, np.int64), 'values_unsorted': np.zeros(rendered, np.uint32)}
            cases.append(Case({'P': (count, 1, 1), 'grid': grid, 'rendered': (rendered, 1, 1)}, inputs, {},
                              f'{count} voxels, {rendered} tile instances, {w}x{h}'))
        elif 'point_list' in names:  # render: each tile's voxels, in list order (first hit wins)
            view, _ = camera(rng)
            tiles = grid[0] * grid[1]
            channels = int(rng.integers(1, 4))
            voxels = max(count // 4, 1)
            depth = rng.uniform(1.5, 5.0, voxels)
            spread = rng.uniform(-0.6, 0.6, (voxels, 2)) * depth[:, None]
            positions = np.column_stack([spread, depth]).astype(np.float32)
            lists, ranges = [], np.zeros(2 * tiles, np.uint32)
            for t in range(tiles):
                chosen = rng.choice(voxels, int(rng.integers(0, min(voxels, 70) + 1)), replace=True) if rng.random() < 0.85 else []
                ranges[2 * t], ranges[2 * t + 1] = sum(len(x) for x in lists), sum(len(x) for x in lists) + len(chosen)
                lists.append(np.asarray(chosen, np.uint32))
            point_list = np.concatenate(lists).astype(np.uint32) if sum(len(x) for x in lists) else np.zeros(1, np.uint32)
            inputs = {'ranges': ranges, 'point_list': point_list, 'cam_pos': rng.uniform(-0.1, 0.1, 3).astype(np.float32),
                      'viewmatrix': view, 'positions': positions.reshape(-1),
                      'attrs': rng.uniform(0, 1, voxels * channels).astype(np.float32),
                      'out_color': np.zeros(channels * w * h, np.float32), 'out_depth': np.zeros(w * h, np.float32),
                      'out_alpha': np.zeros(w * h, np.float32)}
            values = {'C': channels, 'W': w, 'H': h, 'tan_fovx': float(np.float32(rng.uniform(0.3, 0.7))),
                      'tan_fovy': float(np.float32(rng.uniform(0.3, 0.7))), 'voxel_size': float(np.float32(rng.uniform(0.15, 0.6)))}
            # depth is the original's uninitialized D where no voxel is hit: defined only where alpha is 1
            cases.append(Case({'image': (w, h, 1), 'color': (w, h, channels), 'tiles': (tiles, 1, 1),
                               'rendered': (len(point_list), 1, 1), 'voxels': (voxels, 1, 1),
                               'attributes': (voxels * channels, 1, 1)}, inputs, values,
                              f'{voxels} voxels, {len(point_list)} in tiles, {w}x{h}x{channels}', defined={'out_depth': 'out_alpha'}))
        else:  # identifyTileRanges
            tiles = grid[0] * grid[1]
            tile = np.sort(rng.integers(0, tiles, count)).astype(np.int64)
            depth_bits = rng.uniform(0.2, 9.0, count).astype(np.float32).view(np.uint32).astype(np.int64)
            keys = np.sort((tile << 32) | depth_bits)
            inputs = {'point_list_keys': keys, 'ranges': np.zeros(2 * tiles, np.uint32)}
            cases.append(Case({'L': (count, 1, 1), 'tiles': (tiles, 1, 1)}, inputs, {}, f'{count} keys, {tiles} tiles'))
    return cases


PREFIXES['trellis2_raster_'] = raster_cases


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
