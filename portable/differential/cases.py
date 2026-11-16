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


def exact_sums(rng, plan, inputs):
    """Atomic float additions happen in no fixed order (NUMERICS.md, Atomics). When a program has a
    float32 atomic buffer, its float32 inputs are quarter-integers of small size, whose sums are
    exact in any order."""
    if 'buffer<f32, atomic>' not in {r['type'] for r in plan['resources']}:
        return
    for name, array in inputs.items():
        if array.dtype == np.float32:
            inputs[name] = (rng.integers(-256, 257, array.size) / 4).astype(np.float32)


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
        inputs = {r['name']: rng.integers(0, 2**64 - 1, buffer_length(r, sizes), dtype=np.uint64, endpoint=True)
                  if r['type'] == 'buffer<u64, atomic>' else random_array(rng, DTYPES[r['type']], buffer_length(r, sizes))
                  for r in plan['resources'] if r['kind'] in ('input', 'inout')}
        exact_sums(rng, plan, inputs)
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


def scale_offset(plan, seed, shapes=SHAPES):
    rng = np.random.default_rng(seed)
    return generic(plan, seed, shapes, values={'offset': lambda: int(rng.integers(-1000, 1001)),
                                       'scale': lambda: float(np.float32(rng.uniform(-8, 8)))})


def sam2_cc(plan, seed, shapes=None):
    """Masks of varied density on even sizes, as SAM 2 requires; one image per call. Given
    shapes, one image of each shape's even W x H."""
    rng = np.random.default_rng(seed)
    cases = []
    sizes = [(2, 2), (4, 6), (16, 16), (34, 8), (64, 48), (130, 66)]*3
    if shapes:
        sizes = [(max(2, s[0] // 2 * 2), max(2, s[1] // 2 * 2)) for s in shapes]
    for k, (w, h) in enumerate(sizes):
        density = (k*29) % 101
        values = rng.integers(1, 256, w*h).astype(np.uint8)
        mask = np.where(rng.integers(0, 100, w*h) < density, values, 0).astype(np.uint8)
        cases.append(Case({'shape': (w, h, 1)}, {'img': mask}, label=f'{w}x{h} density {density}'))
    return cases


def grid_stride(plan, seed, shapes=SHAPES):
    """n is the number of elements to write, at most the buffers' size."""
    rng = np.random.default_rng(seed)
    cases = generic(plan, seed, shapes)
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


def mmcv_roi_align(plan, seed, shapes=None):
    """mmcv's RoIAlign forward: feature maps (N, C, H, W), RoIs (batch index, x1, y1, x2, y2) in image
    coordinates (feature coordinates / spatial_scale), some partly or wholly outside the map and
    some degenerate; max and average pooling, aligned or not, fixed or adaptive sampling. The
    outputs start at zero, as mmcv allocates them (new_zeros)."""
    rng = np.random.default_rng(seed)
    configs = [  # (images, channels, height, width, rois, pooled h, pooled w, sampling, mode, aligned, scale)
        (1, 1, 5, 7, 1, 2, 2, 2, 0, 0, 1.0),
        (2, 3, 9, 6, 5, 3, 2, 0, 1, 1, 0.5),
        (1, 4, 16, 16, 7, 7, 7, 2, 1, 0, 0.25),
        (3, 2, 11, 13, 9, 2, 3, 0, 0, 1, 0.125),
        (2, 8, 20, 17, 12, 4, 4, 1, 1, 1, 1.0 / 16),
    ]
    out = []
    for n, c, h, w, k, ph, pw, sampling, mode, aligned, scale in configs:
        boxes = np.empty((k, 5), np.float32)
        boxes[:, 0] = rng.integers(0, n, k)
        # most inside the map; one in four an edge case: partly or wholly outside, or degenerate
        corner = rng.uniform(0, 1, (k, 2)) * [w - 1, h - 1]
        size = rng.uniform(0.5, 1, (k, 2)) * ([w, h] - corner)
        edge = rng.random(k) < 0.25
        edge[0] = False  # the first RoI of every case lies inside
        corner[edge] = rng.uniform(-3, max(w, h) + 3, (int(edge.sum()), 2))
        size[edge] = rng.uniform(-2, max(w, h) / 2, (int(edge.sum()), 2))
        boxes[:, 1:3] = corner / scale
        boxes[:, 3:5] = (corner + size) / scale
        count = k * c * ph * pw
        shapes_ = {'pooled': (pw * ph, c, k), 'feature': (w, h, c * n), 'boxes': (k, 1, 1)}
        inputs = {'input': rng.standard_normal(n * c * h * w).astype(np.float32), 'rois': boxes.reshape(-1),
                  'output': np.zeros(count, np.float32), 'argmax_y': np.zeros(count, np.float32),
                  'argmax_x': np.zeros(count, np.float32)}
        values = {'nthreads': count, 'pooled_height': ph, 'pooled_width': pw, 'spatial_scale': scale,
                  'sampling_ratio': sampling, 'pool_mode': mode, 'aligned': aligned, 'channels': c,
                  'height': h, 'width': w}
        out.append(Case(shapes_, inputs, values, f'{n}x{c}x{h}x{w} rois={k} {ph}x{pw} '
                                                 f'{"max" if mode == 0 else "avg"}{" aligned" if aligned else ""}'))
    return out


def mmcv_roi_pool(plan, seed, shapes=None):
    """mmcv's RoIPool forward: max over each bin of RoIs (batch index, x1, y1, x2, y2), some partly
    or wholly outside the map and some degenerate. Half the cases leave the optional argmax out
    (IR version 9): the kernel tests it against NULL, so the original gets a null pointer too."""
    rng = np.random.default_rng(seed)
    configs = [(1, 1, 5, 7, 1, 2, 2, 1.0), (2, 3, 9, 6, 5, 3, 2, 0.5), (1, 4, 16, 16, 7, 7, 7, 0.25),
               (3, 2, 11, 13, 9, 2, 3, 0.125), (2, 8, 20, 17, 12, 4, 4, 1.0 / 16), (1, 2, 6, 6, 4, 3, 3, 1.0)]
    out = []
    for i, (n, c, h, w, k, ph, pw, scale) in enumerate(configs):
        boxes = np.empty((k, 5), np.float32)
        boxes[:, 0] = rng.integers(0, n, k)
        corner = rng.uniform(0, 1, (k, 2)) * [w - 1, h - 1]
        size = rng.uniform(0.5, 1, (k, 2)) * ([w, h] - corner)
        edge = rng.random(k) < 0.25
        edge[0] = False
        corner[edge] = rng.uniform(-3, max(w, h) + 3, (int(edge.sum()), 2))
        size[edge] = rng.uniform(-2, max(w, h) / 2, (int(edge.sum()), 2))
        boxes[:, 1:3] = corner / scale
        boxes[:, 3:5] = (corner + size) / scale
        count = k * c * ph * pw
        keep_argmax = i % 2 == 0
        inputs = {'input': rng.standard_normal(n * c * h * w).astype(np.float32), 'rois': boxes.reshape(-1),
                  'output': np.zeros(count, np.float32)}
        if keep_argmax:
            inputs['argmax'] = np.zeros(count, np.int32)
        values = {'nthreads': count, 'pooled_height': ph, 'pooled_width': pw, 'spatial_scale': scale,
                  'channels': c, 'height': h, 'width': w}
        out.append(Case({'pooled': (pw * ph, c, k), 'feature': (w, h, c * n), 'boxes': (k, 1, 1)}, inputs, values,
                        f'{n}x{c}x{h}x{w} rois={k} {ph}x{pw}{" argmax" if keep_argmax else " (no argmax)"}'))
    return out


def mmcv_nms(plan, seed, shapes=None):
    """mmcv's NMS mask kernel: boxes (x1, y1, x2, y2) in clusters, so that many overlap, sorted as
    the host sorts them (by score, here by a random score); counts around the 64-box blocks; both
    IoU offsets mmcv uses (0 and 1). The mask starts at zero (the host's at::empty leaves the
    words the kernel does not write undefined, and never reads them)."""
    rng = np.random.default_rng(seed)
    out = []
    for n, threshold, offset in ((1, 0.5, 0), (5, 0.3, 1), (64, 0.5, 0), (65, 0.4, 0), (130, 0.6, 1), (300, 0.5, 0)):
        centres = rng.uniform(0, 100, (max(1, n // 6), 2))
        centre = centres[rng.integers(0, len(centres), n)] + rng.normal(0, 4, (n, 2))
        size = rng.uniform(4, 30, (n, 2))
        boxes = np.concatenate([centre - size / 2, centre + size / 2], axis=1).astype(np.float32)
        boxes = boxes[np.argsort(-rng.random(n), kind='stable')]
        blocks = (n + 63) // 64
        out.append(Case({'boxes': (n, 1, 1), 'mask': (n * blocks, 1, 1)},
                        {'dev_boxes': boxes.reshape(-1), 'dev_mask': np.zeros(n * blocks, np.uint64)},
                        {'n_boxes': n, 'iou_threshold': threshold, 'offset': offset},
                        f'{n} boxes iou>{threshold} offset {offset}'))
    return out


def csr(rng, rows, columns):
    """A random CSR pattern (rowptr, col) as torch_sparse stores it, int64: rows with no entries,
    with fewer than 32 and with more (a warp reads a row's entries 32 at a time), columns repeated."""
    lengths = rng.integers(0, 12, rows)
    lengths[rng.random(rows) < 0.2] = 0
    long = rng.random(rows) < 0.2
    lengths[long] = rng.integers(30, 80, int(long.sum()))
    rowptr = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
    col = rng.integers(0, columns, max(1, int(rowptr[-1]))).astype(np.int64)
    return rowptr, col


SPMM_SIZES = [(1, 1, 1, 1), (1, 5, 7, 3), (2, 9, 40, 33), (1, 40, 50, 70), (3, 17, 8, 32)]  # (B, M, N, K)


def sparse_spmm(plan, seed, shapes=None):
    """torch_sparse's spmm (out = A @ mat over a CSR A, reduced by sum, mean, min or max): batches of
    dense matrices, rows of every length (none, a few, more than one warp's 32), K around the 32
    columns a block row covers. out starts at zero and arg_out at the number of entries (torch_sparse's
    full_like(out, col.numel()), the index of no entry)."""
    rng = np.random.default_rng(seed)
    out = []
    for b, m, n, k in SPMM_SIZES:
        rowptr, col = csr(rng, m, n)
        e = col.size
        inputs = {'rowptr_data': rowptr, 'col_data': col, 'value_data': rng.standard_normal(e).astype(np.float32),
                  'mat_data': rng.standard_normal(b * n * k).astype(np.float32),
                  'out_data': np.zeros(b * m * k, np.float32), 'arg_out_data': np.full(b * m * k, e, np.int64)}
        out.append(Case({'out': (k, m, b), 'rows': (m + 1, 1, 1), 'nnz': (e, 1, 1), 'mat': (k, n, b)}, inputs,
                        {'B': b, 'M': m, 'N': n, 'K': k}, f'{b}x({m}x{n} sparse, {int(rowptr[-1])} entries) @ {n}x{k}'))
    return out


def sparse_spmm_value_bw(plan, seed, shapes=None):
    """torch_sparse's gradient of spmm by the sparse values: one warp per entry (row, col) adds
    mat[b, col, :] * grad[b, row, :] over the batch and K, then a shuffle reduction; mean divides by
    the row's length. out starts at zero (torch::zeros)."""
    rng = np.random.default_rng(seed)
    out = []
    for b, m, n, k in SPMM_SIZES:
        rowptr, col = csr(rng, m, n)
        e = int(rowptr[-1])
        if e == 0:
            rowptr[1:] = 1  # at least one entry: the first row's
            e = 1
        row = np.repeat(np.arange(m), np.diff(rowptr)).astype(np.int64)
        inputs = {'row_data': row, 'rowptr_data': rowptr, 'col_data': col[:e],
                  'mat_data': rng.standard_normal(b * n * k).astype(np.float32),
                  'grad_data': rng.standard_normal(b * m * k).astype(np.float32), 'out_data': np.zeros(e, np.float32)}
        out.append(Case({'edges': (e, 1, 1), 'rows': (m + 1, 1, 1), 'mat': (k, n, b), 'grad': (k, m, b)}, inputs,
                        {'B': b, 'M': m, 'N': n, 'E': e, 'K': k}, f'{e} entries of {m}x{n}, batch {b}, K {k}'))
    return out


SPECIAL = {'scale_offset': scale_offset, 'sam2_cc': sam2_cc, 'grid_stride': grid_stride,
           'mmcv_roi_align_forward': mmcv_roi_align, 'mmcv_nms': mmcv_nms, 'mmcv_roi_pool_forward': mmcv_roi_pool,
           'pytorch_sparse_spmm_sum': sparse_spmm, 'pytorch_sparse_spmm_mean': sparse_spmm,
           'pytorch_sparse_spmm_max': sparse_spmm, 'pytorch_sparse_spmm_value_bw_mean': sparse_spmm_value_bw}
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


def input_spec(spec):
    """(element type, None or (low, high)) of a program's input spec: 'f32', or 'i32 0..7' for
    integers drawn from that inclusive range (indices that must be in range to mean anything)."""
    element, _, bounds = spec.partition(' ')
    if not bounds:
        return element, None
    low, high = bounds.split('..')
    return element, (int(low), int(high))


def for_program(name, plan, seed=20261111, values=None, shapes=None, inputs=None):
    """Cases for a program. `values` constrains scalar arguments: 'volume' is the number of
    elements of the dispatch shape (a count the buffers must hold), an integer is used as is,
    and any other string is an expression of the shape's W, H and N ('W/4', 'W*H').
    'args.ne0' names a field of a struct argument."""
    if shapes:  # a corpus program's own sizes (whole rows of a byte buffer), or the benchmark's
        cases = SPECIAL.get(name, generic)(plan, seed, shapes=[tuple(s) for s in shapes])
    else:
        family = next((f for prefix, f in PREFIXES.items() if name.startswith(prefix)), None)
        cases = SPECIAL.get(name, family or generic)(plan, seed)
    rng = np.random.default_rng(seed + 1)
    for buffer_name, spec in (inputs or {}).items():  # byte buffers holding values of another type
        element, bounds = input_spec(spec)
        dtype = DTYPES[element]
        for case in cases:
            raw = case.inputs[buffer_name]
            count = raw.size*raw.itemsize // np.dtype(dtype).itemsize
            drawn = (random_array(rng, dtype, count) if bounds is None else
                     rng.integers(bounds[0], bounds[1], count, dtype=dtype, endpoint=True))
            case.inputs[buffer_name] = drawn.view(raw.dtype)
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


# --- Benchmark-size cases for TRELLIS.2's families (vectorised: millions of keys) ---

def distinct_slots(rng, draw, slots, bits, count):
    """`count` of the drawn keys that hash to distinct slots (so distinct keys), in random order."""
    _, first = np.unique(murmur_slot(draw, slots, bits), return_index=True)
    chosen = draw[first]
    if len(chosen) < count:
        raise ValueError(f'only {len(chosen)} keys with distinct slots for {count}')
    return chosen[rng.permutation(len(chosen))[:count]]


def hashmap_bench(plan, seed, count):
    """hashmap_cases at `count` keys in 2 x count slots (TRELLIS.2 allocates twice the keys)."""
    rng = np.random.default_rng(seed)
    types = {r['name']: DTYPES[r['type']] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    key_type, value_type = types['hashmap_keys'], types['hashmap_values']
    bits, empty = 8 * np.dtype(key_type).itemsize, np.iinfo(key_type).max
    extents = {r['name']: r['extent'] for r in plan['resources'] if r['kind'] not in ('shape', 'value')}
    table_shape, keys_shape = extents['hashmap_keys'], extents['keys']
    slots = count if table_shape == keys_shape else 2 * count
    inserting = any(r['name'] == 'hashmap_keys' and r['kind'] == 'inout' for r in plan['resources'])
    draw = rng.integers(0, empty, 4 * count, dtype=key_type)
    if inserting:
        keys = distinct_slots(rng, draw, slots, bits, count)
        inputs = {'hashmap_keys': np.full(slots, empty, key_type), 'hashmap_values': np.zeros(slots, value_type),
                  'keys': keys, 'values': rng.integers(0, np.iinfo(value_type).max, count, dtype=value_type, endpoint=True)}
    else:
        present = distinct_slots(rng, draw, slots, bits, slots // 2)
        table, stored = np.full(slots, empty, key_type), np.zeros(slots, value_type)
        where = murmur_slot(present, slots, bits).astype(np.int64)
        table[where] = present
        stored[where] = rng.integers(0, np.iinfo(value_type).max, len(present), dtype=value_type)
        probes = np.where(rng.random(count) < 0.5, rng.choice(present, count),
                          rng.integers(0, empty, count, dtype=key_type)).astype(key_type)
        inputs = {'hashmap_keys': table, 'hashmap_values': stored, 'keys': probes}
    return Case({table_shape: (slots, 1, 1), keys_shape: (count, 1, 1)}, inputs, {}, f'{slots} slots, {count} keys')


def hash3d_bench(plan, seed, count):
    """hash3d_cases at `count` coordinates (batch 0-2 in 256 x 256 x 128) in 2 x count slots."""
    rng = np.random.default_rng(seed)
    resources = {r['name']: r for r in plan['resources']}
    keys_name = 'hashmap_keys' if 'hashmap_keys' in resources else 'hashmap'
    values_name = 'hashmap_values' if 'hashmap_values' in resources else 'values'
    key_type, value_type = DTYPES[resources[keys_name]['type']], DTYPES[resources[values_name]['type']]
    bits, empty = 8 * np.dtype(key_type).itemsize, np.iinfo(key_type).max
    table_shape, coords_shape = resources[keys_name]['extent'], resources['coords']['extent']
    slots, (w, h, d) = 2 * count, (256, 256, 128)

    def coordinates(n):
        return np.column_stack([rng.integers(0, 3, n), rng.integers(0, w, n), rng.integers(0, h, n),
                                rng.integers(0, d, n)]).astype(np.int32)

    def keys(c):
        k = (c[:, 0].astype(np.int64) * w * h * d + c[:, 1].astype(np.int64) * h * d + c[:, 2].astype(np.int64) * d
             + c[:, 3])  # below 2**25: the same in 32 and 64 bits
        return k.astype(key_type)
    values = {'W': w, 'H': h, 'D': d}
    if resources[keys_name]['kind'] == 'inout':
        drawn = coordinates(4 * count)
        _, first = np.unique(murmur_slot(keys(drawn), slots, bits), return_index=True)
        coords = drawn[first][rng.permutation(len(first))[:count]]
        inputs = {keys_name: np.full(slots, empty, key_type), values_name: np.zeros(slots, value_type),
                  'coords': coords.reshape(-1)}
        if 'values' in resources and values_name != 'values':
            inputs['values'] = rng.integers(0, np.iinfo(value_type).max, count, dtype=value_type, endpoint=True)
    else:
        drawn = coordinates(2 * slots)
        _, first = np.unique(murmur_slot(keys(drawn), slots, bits), return_index=True)
        present = drawn[first][: slots // 2]
        table, stored = np.full(slots, empty, key_type), np.zeros(slots, value_type)
        where = murmur_slot(keys(present), slots, bits).astype(np.int64)
        table[where] = keys(present)
        stored[where] = rng.integers(0, np.iinfo(value_type).max, len(present), dtype=value_type)
        probes = np.where((rng.random(count) < 0.5)[:, None], present[rng.integers(0, len(present), count)],
                          coordinates(count))
        outside = rng.random(count) < 0.1  # outside the volume on one axis
        axis = rng.integers(1, 4, count)
        probes[outside, axis[outside]] = -1
        inputs = {keys_name: table, values_name: stored, 'coords': probes.astype(np.int32).reshape(-1)}
    return Case({table_shape: (slots, 1, 1), coords_shape: (count, 1, 1)}, inputs, values,
                f'{slots} slots, {count} coordinates')


def raster_bench(plan, seed, size):
    """raster_cases at an image of size[0] x size[1] with size volume / 8 voxels."""
    rng = np.random.default_rng(seed)
    names = {r['name'] for r in plan['resources']}
    w, h = size[0], size[1]
    count = max(volume(size) // 8, 1)
    grid = (-(-w // TILE), -(-h // TILE), 1)
    tiles = grid[0] * grid[1]
    if 'positions' in names and 'point_list' not in names:  # preprocess
        view, proj = camera(rng)
        depth = rng.uniform(-0.5, 6.0, count)
        spread = rng.uniform(-1.2, 1.2, (count, 2)) * np.maximum(depth, 0.3)[:, None]
        inputs = {'positions': np.column_stack([spread, depth]).astype(np.float32).reshape(-1), 'viewmatrix': view,
                  'projmatrix': proj, 'bboxes': np.zeros(4 * count, np.int32), 'depths': np.zeros(count, np.float32),
                  'tiles_touched': np.zeros(count, np.uint32)}
        values = {'voxel_size': float(np.float32(rng.uniform(0.002, 0.01))), 'width': w, 'height': h}
        return Case({'num_nodes': (count, 1, 1), 'grid': grid}, inputs, values, f'{count} voxels, {w}x{h}')
    if 'offsets' in names:  # duplicateWithKeys: boxes up to 32 pixels, 80% non-empty
        x0, y0 = rng.integers(0, w, count), rng.integers(0, h, count)
        x1 = np.minimum(x0 + rng.integers(1, 33, count), w)
        y1 = np.minimum(y0 + rng.integers(1, 33, count), h)
        lx, ly = np.minimum(grid[0], x0 // TILE), np.minimum(grid[1], y0 // TILE)
        hx, hy = np.minimum(grid[0], (x1 + TILE - 1) // TILE), np.minimum(grid[1], (y1 + TILE - 1) // TILE)
        keep = rng.random(count) < 0.8
        boxes = np.where(keep[:, None], np.column_stack([x0, y0, x1, y1]), 0).astype(np.int32)
        touched = np.where(keep, (hx - lx) * (hy - ly), 0)
        offsets = np.cumsum(touched).astype(np.int64)
        rendered = int(offsets[-1])
        inputs = {'depths': rng.uniform(0.2, 9.0, count).astype(np.float32), 'offsets': offsets,
                  'bboxes': boxes.reshape(-1), 'keys_unsorted': np.zeros(rendered, np.int64),
                  'values_unsorted': np.zeros(rendered, np.uint32)}
        return Case({'P': (count, 1, 1), 'grid': grid, 'rendered': (rendered, 1, 1)}, inputs, {},
                    f'{count} voxels, {rendered} tile instances, {w}x{h}')
    if 'point_list' in names:  # render: up to 70 voxels per tile, 85% of tiles
        view, _ = camera(rng)
        channels, voxels = 3, max(count // 4, 1)
        depth = rng.uniform(1.5, 5.0, voxels)
        spread = rng.uniform(-0.6, 0.6, (voxels, 2)) * depth[:, None]
        lengths = np.where(rng.random(tiles) < 0.85, rng.integers(0, 71, tiles), 0)
        ends = np.cumsum(lengths)
        ranges = np.column_stack([ends - lengths, ends]).astype(np.uint32).reshape(-1)
        point_list = rng.integers(0, voxels, max(int(ends[-1]), 1)).astype(np.uint32)
        inputs = {'ranges': ranges, 'point_list': point_list, 'cam_pos': rng.uniform(-0.1, 0.1, 3).astype(np.float32),
                  'viewmatrix': view, 'positions': np.column_stack([spread, depth]).astype(np.float32).reshape(-1),
                  'attrs': rng.uniform(0, 1, voxels * channels).astype(np.float32),
                  'out_color': np.zeros(channels * w * h, np.float32), 'out_depth': np.zeros(w * h, np.float32),
                  'out_alpha': np.zeros(w * h, np.float32)}
        values = {'C': channels, 'W': w, 'H': h, 'tan_fovx': 0.5, 'tan_fovy': 0.5, 'voxel_size': 0.01}
        return Case({'image': (w, h, 1), 'color': (w, h, channels), 'tiles': (tiles, 1, 1),
                     'rendered': (len(point_list), 1, 1), 'voxels': (voxels, 1, 1),
                     'attributes': (voxels * channels, 1, 1)}, inputs, values,
                    f'{voxels} voxels, {len(point_list)} in tiles, {w}x{h}x{channels}', defined={'out_depth': 'out_alpha'})
    tile = np.sort(rng.integers(0, tiles, count)).astype(np.int64)  # identifyTileRanges
    depth_bits = rng.uniform(0.2, 9.0, count).astype(np.float32).view(np.uint32).astype(np.int64)
    inputs = {'point_list_keys': np.sort((tile << 32) | depth_bits), 'ranges': np.zeros(2 * tiles, np.uint32)}
    return Case({'L': (count, 1, 1), 'tiles': (tiles, 1, 1)}, inputs, {}, f'{count} keys, {tiles} tiles')


BENCH_FAMILIES = {'trellis2_hashmap_': lambda plan, seed, size: hashmap_bench(plan, seed, max(volume(size) // 8, 1)),
                  'trellis2_hash3d_': lambda plan, seed, size: hash3d_bench(plan, seed, max(volume(size) // 8, 1)),
                  'trellis2_raster_': raster_bench}


def scaled_case(name, plan, options, size):
    """One case at the dispatch shape `size`, with the program's own value rules (benchmarks).
    A program sized in bytes (llama.cpp rows: its shape's W counts bytes of a typed byte buffer)
    gets W elements. TRELLIS.2's hash tables and rasteriser get their benchmark generators
    (BENCH_FAMILIES); None for other families, and for programs that size themselves without
    byte rows."""
    for prefix, family in BENCH_FAMILIES.items():
        if name.startswith(prefix):
            return family(plan, 20261111, tuple(size))
    if any(name.startswith(prefix) for prefix in PREFIXES):
        return None
    typed = {**(options.get('inputs') or {}), **(options.get('outputs') or {})}
    if options.get('shapes'):
        if not typed:
            return None
        element = max(np.dtype(DTYPES[input_spec(t)[0]]).itemsize for t in typed.values())
        size = (size[0] * element,) + tuple(size[1:])
    return for_program(name, plan, values=options.get('values'), shapes=[tuple(size)],
                       inputs=options.get('inputs'))[0]


def edge_values(dtype, subnormals=True):
    """The values most likely to separate two implementations of one kernel, per element type.
    subnormals=False leaves them out: Apple GPUs flush them to zero (NUMERICS.md), so a kernel
    given one gives different results on Metal and on CUDA whatever the port does."""
    dtype = np.dtype(dtype)
    if dtype.kind == 'f':
        info = np.finfo(dtype)
        tiny, sub = float(info.tiny), float(info.tiny) / 4  # smallest normal; a subnormal
        values = [np.nan, np.inf, -np.inf, 0.0, -0.0, 1.0, -1.0, 0.5, -0.5, float(info.max), -float(info.max),
                  tiny, -tiny, float(info.eps), 1 + float(info.eps), 2.0**11 + 1, -(2.0**11 + 1)]
        return np.array(values + ([sub, -sub] if subnormals else []), dtype)
    info = np.iinfo(dtype)
    values = [0, 1, info.max, info.max - 1, info.min, info.min + 1, info.max // 2, info.max // 2 + 1]
    if info.min < 0:
        values += [-1, -2]
    return np.array(values, dtype)


def structured(name):
    """True for programs whose inputs hold structure the cases build (hash tables, images of
    components, a rasteriser's scene): edge values mixed into them would make inputs the
    original never meets, such as a hash table with no free slot, whose lookups never end."""
    return name == 'sam2_cc' or any(name.startswith(prefix) for prefix in PREFIXES)


def with_edges(cases, plan, seed, subnormals=True):
    """Copies of the cases with at least 17 elements per buffer whose input buffers mix edge
    values (edge_values) into their random ones: about one element in three, at random places."""
    rng = np.random.default_rng(seed + 2)
    resources = {r['name']: r for r in plan['resources']}
    out = []
    for case in cases:
        buffers = {name: a for name, a in case.inputs.items() if resources.get(name, {}).get('kind') in ('input', 'inout')}
        if not buffers or min(np.asarray(a).size for a in buffers.values()) < 17:
            continue
        inputs = dict(case.inputs)
        for name, array in buffers.items():
            array = np.array(array, copy=True)
            pool = edge_values(array.dtype, subnormals)
            where = rng.random(array.size) < 1 / 3
            array.reshape(-1)[where] = rng.choice(pool, int(where.sum()))
            inputs[name] = array
        out.append(Case(case.shapes, inputs, dict(case.values), f'{case.label} edge values', case.defined))
    return out
