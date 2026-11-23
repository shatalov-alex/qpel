"""Device-wide library calls as qpel pipelines (LIBRARIES.md, device-wide CUB).

A host call such as `cub::DeviceScan::InclusiveSum(temp, bytes, in, out, n)` launches several
kernels with temporary storage between them. qpel writes their bodies as plain CUDA in
frontends/include/libraries/qpel_library/; this module writes, per call, the kernels (one __global__
wrapper per stage, named after the call's prefix, declaring the shared memory and calling a body)
and the launch plan of their stages, the temporary storage as scratch buffers sized per tile of the
input (IR version 21). The importer reads them like any other source.

    source, plan = device_scan('u32')                 # a pipeline of its own
    source, plan = expand(source_text, plan_with_library_stages)   # inside a port's own pipeline

A plan stage {"library": "cub::DeviceScan::InclusiveSum", "input": "i32", "element": "i64",
"bindings": {"in": ..., "out": ..., "n": shape}} (or "cub::DeviceScan::ExclusiveSum", or
"cub::DeviceRadixSort::SortPairs" with "key" and "value" and bindings keys_in, keys_out, values_in,
values_out, begin_bit, end_bit, n; "cub::DeviceSegmentedRadixSort::SortPairs" with "offset" too and bindings
begin_offsets, end_offsets and segments, a shape, besides those; "cub::DeviceMergeSort::SortPairs" or
StableSortPairs with "compare": "less" and bindings keys, values, n, sorted in place) stands for the call; expand() replaces it with the call's stages,
adds its scratch buffers to the plan (named after its prefix) and its kernels to the source.
"""
CUDA_TYPES = {'u32': 'unsigned int', 'i32': 'int', 'u64': 'unsigned long long', 'i64': 'long long'}
SCAN_TILE = 1024  # elements per group of the scan kernels (device_scan.cuh: 256 threads x 4 items)
RADIX_TILE = 256  # keys per group of the sort kernels (device_radix_sort.cuh: one per thread)
RADIX_DIGITS = 256  # 8 bits a pass
VALUE_TYPES = dict(CUDA_TYPES, f32='float')
BITS = {'u32': 32, 'i32': 32, 'u64': 64, 'i64': 64}
INCLUDES = ('#include <qpel_library/device_scan.cuh>\n#include <qpel_library/device_radix_sort.cuh>\n'
            '#include <qpel_library/device_segmented_sort.cuh>\n')


def _stage(kernel, groups, bindings, grid='n', block=256):
    return {'kernel': kernel, 'grid': grid, 'blockSize': block, 'completionBarrier': True, 'domain': 'groups',
            'blockShape': [block, 1, 1], 'groups': [groups, '1', '1'],
            'bindings': [{'parameter': p, 'resource': r} for p, r in bindings]}


def _buffer(kind, name, element, extent='n', **more):
    # (write buffers throughout: the kernels' buffers are not const, see device_scan.cuh)
    return dict({'name': name, 'kind': kind, 'type': f'buffer<{element}, write>', 'extent': extent}, **more)


def scan_kernels(prefix, source, element, exclusive=False, per=1, scale=1):
    """CUDA of the scan's three kernels, <prefix>_reduce, _partials and _tiles, on ceil(volume(n) / per) * scale
    elements of type `source` summed as `element`."""
    i, t = CUDA_TYPES[source], CUDA_TYPES[element]
    shape = f'{per}u, {scale}u'
    return f'''__global__ void {prefix}_reduce({i}* in, {t}* partials, uint3 n) {{
  __shared__ {t} sums[QLIB_SCAN_THREADS];
  qlib_scan_reduce_body<{i}, {t}, {shape}>(sums, in, partials, n);
}}
__global__ void {prefix}_partials({t}* partials, uint3 n) {{  // one group: the totals' exclusive prefix, in place
  __shared__ {t} s[QLIB_SCAN_THREADS];
  const unsigned count = (n.x * n.y * n.z + {per - 1}u) / {per}u * {scale}u;
  const unsigned tiles = (count + QLIB_SCAN_TILE - 1u) / QLIB_SCAN_TILE;
  const unsigned t = threadIdx.x;
  {t} carry = ({t})0;
  for (unsigned first = 0u; first < tiles; first = first + QLIB_SCAN_THREADS) {{
    const {t} x = first + t < tiles ? partials[first + t] : ({t})0;
    {t} total = ({t})0;
    const {t} exclusive = qlib_block_exclusive_sum(s, x, total);
    if (first + t < tiles) {{
      partials[first + t] = qlib_add(carry, exclusive);
    }}
    carry = qlib_add(carry, total);
  }}
}}
__global__ void {prefix}_tiles({i}* in, {t}* out, {t}* partials, uint3 n) {{
  __shared__ {t} s[QLIB_SCAN_THREADS];
  qlib_scan_tiles_body<{i}, {t}, {int(exclusive)}, {shape}>(s, in, out, partials, n);
}}
'''


def scan_stages(prefix, source, target, partials, shape='n', per=1, scale=1):
    """The scan's three stages over ceil(volume(shape) / per) * scale elements of `source` into `target`."""
    count = 'W*H*N' if per == scale == 1 else f'(W*H*N + {per - 1})/{per}*{scale}'
    tiles = f'({count} + {SCAN_TILE - 1})/{SCAN_TILE}'
    return [_stage(f'{prefix}_reduce', tiles, [('in', source), ('partials', partials), ('n', shape)], grid=shape),
            _stage(f'{prefix}_partials', '1', [('partials', partials), ('n', shape)], grid=shape),
            _stage(f'{prefix}_tiles', tiles, [('in', source), ('out', target), ('partials', partials), ('n', shape)], grid=shape)]


def _check_scan(source, element):
    if element not in CUDA_TYPES or source not in CUDA_TYPES:
        raise ValueError(f'device-wide scans are of integers ({", ".join(CUDA_TYPES)}), not {source} into {element}: '
                         "CUB's float scans add in an order that varies from run to run")


def device_scan(element, exclusive=False, source=None, module=None):
    """(CUDA source, launch plan) of cub::DeviceScan::InclusiveSum (or ExclusiveSum) over the n elements of
    `in` (of type `source`, by default `element`) into `out`; `partials` is its scratch, one element per tile."""
    source = source or element
    _check_scan(source, element)
    name = module or f'qlib_device_scan_{element}' + (f'_from_{source}' if source != element else '') + ('_exclusive' if exclusive else '')
    text = (f'// cub::DeviceScan::{"ExclusiveSum" if exclusive else "InclusiveSum"} on {CUDA_TYPES[source]} into '
            f'{CUDA_TYPES[element]}, as qpel\'s library computes it\n' + INCLUDES + scan_kernels('qlib_scan', source, element, exclusive))
    plan = {'schema': 2, 'module': name,
            'resources': [{'name': 'n', 'kind': 'shape', 'type': 'shape3', 'extent': None},
                          _buffer('input', 'in', source), _buffer('output', 'out', element),
                          _buffer('scratch', 'partials', element, per=SCAN_TILE)],
            'stages': scan_stages('qlib_scan', 'in', 'out', 'partials')}
    return text, plan


def sort_kernels(prefix, key, value, bits=None):
    """CUDA of the sort's kernels: <prefix>_histogram, _scatter, _advance and the scan of its counts (<prefix>_scan_*).
    With bits, (begin_bit, end_bit) are those constants instead of parameters."""
    k, v = CUDA_TYPES[key], VALUE_TYPES[value]
    params = ' unsigned int begin_bit,\n                                 unsigned int end_bit,' if bits is None else ''
    given = 'begin_bit, end_bit' if bits is None else f'{bits[0]}u, {bits[1]}u'
    return f'''__global__ void {prefix}_histogram({k}* keys, unsigned int* counts, unsigned int* state,{params} uint3 n) {{
  __shared__ unsigned int digits[QLIB_RADIX_THREADS];
  qlib_radix_histogram_body<{k}>(digits, keys, counts, state, {given}, n);
}}
__global__ void {prefix}_scatter({k}* keys_in, {k}* keys_out, {v}* values_in, {v}* values_out, unsigned int* starts,
                               unsigned int* state,{params} uint3 n) {{
  __shared__ unsigned int digits[QLIB_RADIX_THREADS];
  qlib_radix_scatter_body<{k}, {v}>(digits, keys_in, keys_out, values_in, values_out, starts, state, {given}, n);
}}
__global__ void {prefix}_advance(unsigned int* state) {{
  qlib_radix_advance_body(state);
}}
''' + scan_kernels(f'{prefix}_scan', 'u32', 'u32', True, RADIX_TILE, RADIX_DIGITS)


def sort_plan(prefix, key, value, names=None, bits=None):
    """(resources, stages) of cub::DeviceRadixSort::SortPairs: names maps its own resources (keys_in,
    keys_out, values_in, values_out, begin_bit, end_bit, n) to a plan's; its scratch is <prefix>_*. With bits
    (the kernels of sort_kernels(..., bits)), the bit range is no resource."""
    names = dict({r: r for r in ('keys_in', 'keys_out', 'values_in', 'values_out', 'begin_bit', 'end_bit', 'n')}, **(names or {}))
    own = lambda r: f'{prefix}_{r}'
    n = names['n']
    tiles = f'(W*H*N + {RADIX_TILE - 1})/{RADIX_TILE}'
    stages = []
    for p in range(BITS[key] // 8):  # an even number of passes: the last writes keys_out
        keys = (names['keys_in'] if p == 0 else own('keys') if p % 2 else names['keys_out'], own('keys') if p % 2 == 0 else names['keys_out'])
        values = (names['values_in'] if p == 0 else own('values') if p % 2 else names['values_out'],
                  own('values') if p % 2 == 0 else names['values_out'])
        given = [('state', own('state'))] + ([('begin_bit', names['begin_bit']), ('end_bit', names['end_bit'])] if bits is None else [])
        given.append(('n', n))
        stages.append(_stage(f'{prefix}_histogram', tiles, [('keys', keys[0]), ('counts', own('counts'))] + given, grid=n))
        stages += scan_stages(f'{prefix}_scan', own('counts'), own('starts'), own('partials'), n, RADIX_TILE, RADIX_DIGITS)
        stages.append(_stage(f'{prefix}_scatter', tiles, [('keys_in', keys[0]), ('keys_out', keys[1]), ('values_in', values[0]),
                                                          ('values_out', values[1]), ('starts', own('starts'))] + given, grid=n))
        stages.append(_stage(f'{prefix}_advance', '1', [('state', own('state'))], grid=n, block=32))
    scratch = [_buffer('scratch', own('keys'), key, n), _buffer('scratch', own('values'), value, n),
               _buffer('scratch', own('counts'), 'u32', n, per=RADIX_TILE, scale=RADIX_DIGITS),
               _buffer('scratch', own('starts'), 'u32', n, per=RADIX_TILE, scale=RADIX_DIGITS),
               _buffer('scratch', own('partials'), 'u32', n, per=SCAN_TILE),
               {'name': own('state'), 'kind': 'zeroed', 'type': 'buffer<u32, write>', 'extent': None, 'length': 1}]
    return scratch, stages


def _check_sort(key, value):
    if key not in CUDA_TYPES or value not in VALUE_TYPES:
        raise ValueError(f'radix sort keys are {", ".join(CUDA_TYPES)} and values {", ".join(VALUE_TYPES)}, not {key}, {value}')


def device_radix_sort_pairs(key, value, module=None):
    """(CUDA source, launch plan) of cub::DeviceRadixSort::SortPairs(keys_in, keys_out, values_in, values_out,
    n, begin_bit, end_bit): the n keys sorted by bits [begin_bit, end_bit), stably, their values with them.
    One pass per 8 bits of the key type (passes at or after end_bit copy); keys and values move between
    the outputs and a scratch pair, and an even number of passes ends in the outputs."""
    _check_sort(key, value)
    name = module or f'qlib_radix_sort_pairs_{key}_{value}'
    text = (f'// cub::DeviceRadixSort::SortPairs on {CUDA_TYPES[key]} keys and {VALUE_TYPES[value]} values, as qpel\'s '
            'library computes it\n' + INCLUDES + sort_kernels('qlib_radix', key, value))
    scratch, stages = sort_plan('qlib_radix', key, value)
    plan = {'schema': 2, 'module': name,
            'resources': [{'name': 'n', 'kind': 'shape', 'type': 'shape3', 'extent': None},
                          _buffer('input', 'keys_in', key), _buffer('input', 'values_in', value),
                          _buffer('output', 'keys_out', key), _buffer('output', 'values_out', value), *scratch,
                          {'name': 'begin_bit', 'kind': 'value', 'type': 'u32', 'extent': None},
                          {'name': 'end_bit', 'kind': 'value', 'type': 'u32', 'extent': None}],
            'stages': stages}
    return text, plan


def segmented_kernels(prefix, key, value, offset):
    """CUDA of the segmented sort's kernels (device_segmented_sort.cuh): <prefix>_init, _mark, _gather_places,
    _gather_keys, _gather_values, the sort of the keys (<prefix>_a_*) and of their places (<prefix>_b_*)."""
    k, v, o = CUDA_TYPES[key], VALUE_TYPES[value], CUDA_TYPES[offset]
    gathers = ''.join(f'''__global__ void {prefix}_gather_{what}({t}* from, {t}* to, unsigned int* order, uint3 n) {{
  qlib_gather_body<{t}>(from, to, order, n);
}}
''' for what, t in (('places', 'unsigned int'), ('keys', k), ('values', v)))
    return f'''__global__ void {prefix}_init(unsigned int* positions, unsigned int* order, uint3 n) {{
  qlib_segment_init_body(positions, order, n);
}}
__global__ void {prefix}_mark({o}* begin_offsets, {o}* end_offsets, unsigned int* positions, uint3 segments) {{
  qlib_segment_mark_body<{o}>(begin_offsets, end_offsets, positions, segments);
}}
''' + gathers + sort_kernels(f'{prefix}_a', key, 'u32') + sort_kernels(f'{prefix}_b', 'u32', 'u32', bits=(0, 32))


SEGMENTED = ('keys_in', 'keys_out', 'values_in', 'values_out', 'begin_offsets', 'end_offsets', 'begin_bit', 'end_bit',
             'n', 'segments')


def segmented_plan(prefix, key, value, offset, names=None):
    """(resources, stages) of cub::DeviceSegmentedRadixSort::SortPairs (device_segmented_sort.cuh): names maps its
    own resources (SEGMENTED) to a plan's; segments is a shape, the segments' count; its scratch is <prefix>_*."""
    names = dict({r: r for r in SEGMENTED}, **(names or {}))
    own = lambda r: f'{prefix}_{r}'
    n = names['n']
    tiles = '(W*H*N + 255)/256'
    first, first_stages = sort_plan(f'{prefix}_a', key, 'u32', {
        'keys_in': names['keys_in'], 'keys_out': own('keys'), 'values_in': own('order'), 'values_out': own('order1'),
        'begin_bit': names['begin_bit'], 'end_bit': names['end_bit'], 'n': n})
    second, second_stages = sort_plan(f'{prefix}_b', 'u32', 'u32', {
        'keys_in': own('places'), 'keys_out': own('places_sorted'), 'values_in': own('order1'),
        'values_out': own('order2'), 'n': n}, bits=(0, 32))
    stages = [_stage(f'{prefix}_init', tiles, [('positions', own('positions')), ('order', own('order')), ('n', n)], grid=n),
              _stage(f'{prefix}_mark', tiles, [('begin_offsets', names['begin_offsets']), ('end_offsets', names['end_offsets']),
                                              ('positions', own('positions')), ('segments', names['segments'])],
                     grid=names['segments']),
              *first_stages,
              _stage(f'{prefix}_gather_places', tiles, [('from', own('positions')), ('to', own('places')),
                                                       ('order', own('order1')), ('n', n)], grid=n),
              *second_stages,
              _stage(f'{prefix}_gather_keys', tiles, [('from', names['keys_in']), ('to', names['keys_out']),
                                                     ('order', own('order2')), ('n', n)], grid=n),
              _stage(f'{prefix}_gather_values', tiles, [('from', names['values_in']), ('to', names['values_out']),
                                                       ('order', own('order2')), ('n', n)], grid=n)]
    scratch = [_buffer('scratch', own(r), key if r == 'keys' else 'u32', n)
               for r in ('positions', 'order', 'keys', 'order1', 'places', 'places_sorted', 'order2')]
    return scratch + first + second, stages


def _check_segmented(key, value, offset):
    _check_sort(key, value)
    if offset not in CUDA_TYPES:
        raise ValueError(f'segment offsets are {", ".join(CUDA_TYPES)}, not {offset}')


def device_segmented_radix_sort_pairs(key, value, offset='i64', module=None):
    """(CUDA source, launch plan) of cub::DeviceSegmentedRadixSort::SortPairs(keys_in, keys_out, values_in,
    values_out, n, segments, begin_offsets, end_offsets, begin_bit, end_bit): each segment [begin_offsets[s],
    end_offsets[s]) sorted by bits [begin_bit, end_bit) of its keys, stably, its values with them; the segments
    (a shape) do not overlap, and elements in none are copied in place."""
    _check_segmented(key, value, offset)
    name = module or f'qlib_segmented_sort_pairs_{key}_{value}_{offset}'
    text = (f'// cub::DeviceSegmentedRadixSort::SortPairs on {CUDA_TYPES[key]} keys and {VALUE_TYPES[value]} values, '
            f'{CUDA_TYPES[offset]} offsets, as qpel\'s library computes it\n' + INCLUDES
            + segmented_kernels('qlib_segmented', key, value, offset))
    scratch, stages = segmented_plan('qlib_segmented', key, value, offset)
    plan = {'schema': 2, 'module': name,
            'resources': [{'name': 'n', 'kind': 'shape', 'type': 'shape3', 'extent': None},
                          {'name': 'segments', 'kind': 'shape', 'type': 'shape3', 'extent': None},
                          _buffer('input', 'keys_in', key), _buffer('input', 'values_in', value),
                          _buffer('input', 'begin_offsets', offset, 'segments'),
                          _buffer('input', 'end_offsets', offset, 'segments'),
                          _buffer('output', 'keys_out', key), _buffer('output', 'values_out', value), *scratch,
                          {'name': 'begin_bit', 'kind': 'value', 'type': 'u32', 'extent': None},
                          {'name': 'end_bit', 'kind': 'value', 'type': 'u32', 'extent': None}],
            'stages': stages}
    return text, plan


MERGE_SORTS = ('cub::DeviceMergeSort::SortPairs', 'cub::DeviceMergeSort::StableSortPairs')


def _check_merge(key, value, compare):
    _check_sort(key, value)
    if compare != 'less':
        raise ValueError(f'merge sorts are by less (std::less, cuda::std::less), not {compare}')


def device_merge_sort_pairs(key, value, module=None):
    """(CUDA source, launch plan) of cub::DeviceMergeSort::SortPairs(keys, values, n, less<K>()): the n keys
    sorted in place, their values with them. As a radix sort over every bit of the key type, stable: exactly
    StableSortPairs, and one of the orders SortPairs, which is not stable, may give."""
    _check_merge(key, value, 'less')
    name = module or f'qlib_merge_sort_pairs_{key}_{value}'
    bits = (0, BITS[key])
    text = (f'// cub::DeviceMergeSort::SortPairs on {CUDA_TYPES[key]} keys and {VALUE_TYPES[value]} values by less, '
            'as qpel\'s library computes it\n' + INCLUDES + sort_kernels('qlib_merge', key, value, bits))
    scratch, stages = sort_plan('qlib_merge', key, value, {'keys_in': 'keys', 'keys_out': 'keys', 'values_in': 'values',
                                                           'values_out': 'values'}, bits)
    plan = {'schema': 2, 'module': name,
            'resources': [{'name': 'n', 'kind': 'shape', 'type': 'shape3', 'extent': None},
                          _buffer('inout', 'keys', key), _buffer('inout', 'values', value), *scratch],
            'stages': stages}
    return text, plan


def expand(source_text, plan):
    """(source text, plan) with each library stage of the plan replaced by its call's stages and scratch, and
    its kernels added to the source (see the module's docstring). A plan without library stages is returned as it is."""
    if not any('library' in s for s in plan['stages']):
        return source_text, plan
    resources, stages, kernels = list(plan['resources']), [], []
    taken = {r['name'] for r in resources}
    for number, s in enumerate(plan['stages']):
        if 'library' not in s:
            stages.append(s)
            continue
        prefix = f'qlib{number}'
        while any(name.startswith(prefix + '_') for name in taken):
            prefix += '_'
        b = s['bindings']
        if s['library'] in ('cub::DeviceScan::InclusiveSum', 'cub::DeviceScan::ExclusiveSum'):
            element = s['element']
            source = s.get('input', element)
            _check_scan(source, element)
            kernels.append(scan_kernels(prefix, source, element, s['library'].endswith('ExclusiveSum')))
            resources.append(_buffer('scratch', f'{prefix}_partials', element, b['n'], per=SCAN_TILE))
            stages += scan_stages(prefix, b['in'], b['out'], f'{prefix}_partials', b['n'])
        elif s['library'] == 'cub::DeviceRadixSort::SortPairs':
            _check_sort(s['key'], s['value'])
            kernels.append(sort_kernels(prefix, s['key'], s['value']))
            scratch, more = sort_plan(prefix, s['key'], s['value'], b)
            resources += scratch
            stages += more
        elif s['library'] == 'cub::DeviceSegmentedRadixSort::SortPairs':
            _check_segmented(s['key'], s['value'], s.get('offset', 'i64'))
            kernels.append(segmented_kernels(prefix, s['key'], s['value'], s.get('offset', 'i64')))
            scratch, more = segmented_plan(prefix, s['key'], s['value'], s.get('offset', 'i64'), b)
            resources += scratch
            stages += more
        elif s['library'] in MERGE_SORTS:
            _check_merge(s['key'], s['value'], s.get('compare', 'less'))
            bits = (0, BITS[s['key']])
            kernels.append(sort_kernels(prefix, s['key'], s['value'], bits))
            scratch, more = sort_plan(prefix, s['key'], s['value'], {'keys_in': b['keys'], 'keys_out': b['keys'],
                                                                      'values_in': b['values'], 'values_out': b['values'],
                                                                      'n': b['n']}, bits)
            resources += scratch
            stages += more
        else:
            raise ValueError(f'library call {s["library"]} is not in qpel\'s library (LIBRARIES.md)')
        taken |= {r['name'] for r in resources}
    text = source_text + '\n// qpel\'s library (frontends/library.py)\n' + INCLUDES + ''.join(kernels)
    return text, dict(plan, resources=resources, stages=stages)


def cub_scan_main(element, exclusive=False):
    """A CUDA program calling NVIDIA's cub::DeviceScan on <dir>/in.bin into <dir>/out.bin (GPGPU-Sim, a T4)."""
    t = CUDA_TYPES[element]
    call = 'ExclusiveSum' if exclusive else 'InclusiveSum'
    return f'''#include <cub/cub.cuh>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>
int main(int argc, char** argv) {{
  const std::string dir = argv[1];
  std::ifstream file(dir + "/in.bin", std::ios::binary | std::ios::ate);
  const size_t bytes = (size_t)file.tellg(); file.seekg(0);
  std::vector<{t}> in(bytes / sizeof({t})), out(in.size());
  file.read((char*)in.data(), bytes);
  const int n = (int)in.size();
  {t} *d_in = nullptr, *d_out = nullptr; void* temp = nullptr; size_t temp_bytes = 0;
  cudaMalloc(&d_in, bytes + 1); cudaMalloc(&d_out, bytes + 1);
  cudaMemcpy(d_in, in.data(), bytes, cudaMemcpyHostToDevice);
  cub::DeviceScan::{call}(temp, temp_bytes, d_in, d_out, n);
  cudaMalloc(&temp, temp_bytes);
  cub::DeviceScan::{call}(temp, temp_bytes, d_in, d_out, n);
  if (cudaDeviceSynchronize() != cudaSuccess) {{ std::fprintf(stderr, "cub failed\\n"); return 3; }}
  cudaMemcpy(out.data(), d_out, bytes, cudaMemcpyDeviceToHost);
  std::ofstream(dir + "/out.bin", std::ios::binary).write((const char*)out.data(), bytes);
  return 0;
}}
'''


def cub_sort_main(key, value):
    """A CUDA program calling NVIDIA's cub::DeviceRadixSort::SortPairs on <dir>/keys.bin, values.bin and
    bits.bin (begin_bit, end_bit as two u32) into <dir>/keys_out.bin and values_out.bin."""
    k, v = CUDA_TYPES[key], VALUE_TYPES[value]
    return f'''#include <cub/cub.cuh>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>
template <class T> std::vector<T> load(const std::string& path) {{
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  const size_t bytes = (size_t)file.tellg(); file.seekg(0);
  std::vector<T> out(bytes / sizeof(T)); file.read((char*)out.data(), bytes); return out;
}}
int main(int argc, char** argv) {{
  const std::string dir = argv[1];
  std::vector<{k}> keys = load<{k}>(dir + "/keys.bin");
  std::vector<{v}> values = load<{v}>(dir + "/values.bin");
  std::vector<unsigned int> bits = load<unsigned int>(dir + "/bits.bin");
  const int n = (int)keys.size();
  {k} *k_in, *k_out; {v} *v_in, *v_out; void* temp = nullptr; size_t temp_bytes = 0;
  cudaMalloc(&k_in, n * sizeof({k}) + 1); cudaMalloc(&k_out, n * sizeof({k}) + 1);
  cudaMalloc(&v_in, n * sizeof({v}) + 1); cudaMalloc(&v_out, n * sizeof({v}) + 1);
  cudaMemcpy(k_in, keys.data(), n * sizeof({k}), cudaMemcpyHostToDevice);
  cudaMemcpy(v_in, values.data(), n * sizeof({v}), cudaMemcpyHostToDevice);
  cub::DeviceRadixSort::SortPairs(temp, temp_bytes, k_in, k_out, v_in, v_out, n, (int)bits[0], (int)bits[1]);
  cudaMalloc(&temp, temp_bytes);
  cub::DeviceRadixSort::SortPairs(temp, temp_bytes, k_in, k_out, v_in, v_out, n, (int)bits[0], (int)bits[1]);
  if (cudaDeviceSynchronize() != cudaSuccess) {{ std::fprintf(stderr, "cub failed\\n"); return 3; }}
  cudaMemcpy(keys.data(), k_out, n * sizeof({k}), cudaMemcpyDeviceToHost);
  cudaMemcpy(values.data(), v_out, n * sizeof({v}), cudaMemcpyDeviceToHost);
  std::ofstream(dir + "/keys_out.bin", std::ios::binary).write((const char*)keys.data(), n * sizeof({k}));
  std::ofstream(dir + "/values_out.bin", std::ios::binary).write((const char*)values.data(), n * sizeof({v}));
  return 0;
}}
'''


def cub_segmented_sort_main(key, value, offset='i64'):
    """A CUDA program calling NVIDIA's cub::DeviceSegmentedRadixSort::SortPairs on <dir>/keys.bin, values.bin,
    begin.bin and end.bin (the segments' offsets) and bits.bin (begin_bit, end_bit as two u32) into
    <dir>/keys_out.bin and values_out.bin (elements in no segment: what CUB left there)."""
    k, v, o = CUDA_TYPES[key], VALUE_TYPES[value], CUDA_TYPES[offset]
    return f'''#include <cub/cub.cuh>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>
template <class T> std::vector<T> load(const std::string& path) {{
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  const size_t bytes = (size_t)file.tellg(); file.seekg(0);
  std::vector<T> out(bytes / sizeof(T)); file.read((char*)out.data(), bytes); return out;
}}
template <class T> T* device(const std::vector<T>& host) {{
  T* out; cudaMalloc(&out, host.size() * sizeof(T) + 1);
  cudaMemcpy(out, host.data(), host.size() * sizeof(T), cudaMemcpyHostToDevice); return out;
}}
int main(int argc, char** argv) {{
  const std::string dir = argv[1];
  std::vector<{k}> keys = load<{k}>(dir + "/keys.bin");
  std::vector<{v}> values = load<{v}>(dir + "/values.bin");
  std::vector<{o}> begin = load<{o}>(dir + "/begin.bin"), end = load<{o}>(dir + "/end.bin");
  std::vector<unsigned int> bits = load<unsigned int>(dir + "/bits.bin");
  const int n = (int)keys.size(), segments = (int)begin.size();
  {k} *k_in = device(keys), *k_out = device(keys); {v} *v_in = device(values), *v_out = device(values);
  {o} *d_begin = device(begin), *d_end = device(end); void* temp = nullptr; size_t temp_bytes = 0;
  cub::DeviceSegmentedRadixSort::SortPairs(temp, temp_bytes, k_in, k_out, v_in, v_out, n, segments, d_begin, d_end,
                                           (int)bits[0], (int)bits[1]);
  cudaMalloc(&temp, temp_bytes);
  cub::DeviceSegmentedRadixSort::SortPairs(temp, temp_bytes, k_in, k_out, v_in, v_out, n, segments, d_begin, d_end,
                                           (int)bits[0], (int)bits[1]);
  if (cudaDeviceSynchronize() != cudaSuccess) {{ std::fprintf(stderr, "cub failed\\n"); return 3; }}
  cudaMemcpy(keys.data(), k_out, n * sizeof({k}), cudaMemcpyDeviceToHost);
  cudaMemcpy(values.data(), v_out, n * sizeof({v}), cudaMemcpyDeviceToHost);
  std::ofstream(dir + "/keys_out.bin", std::ios::binary).write((const char*)keys.data(), n * sizeof({k}));
  std::ofstream(dir + "/values_out.bin", std::ios::binary).write((const char*)values.data(), n * sizeof({v}));
  return 0;
}}
'''


def cub_merge_sort_main(key, value, stable=False):
    """A CUDA program calling NVIDIA's cub::DeviceMergeSort::SortPairs (or StableSortPairs) with less on
    <dir>/keys.bin and values.bin, in place, into <dir>/keys_out.bin and values_out.bin."""
    k, v = CUDA_TYPES[key], VALUE_TYPES[value]
    call = 'StableSortPairs' if stable else 'SortPairs'
    return f'''#include <cub/cub.cuh>
#include <cuda/std/functional>
#include <cstdio>
#include <fstream>
#include <string>
#include <vector>
template <class T> std::vector<T> load(const std::string& path) {{
  std::ifstream file(path, std::ios::binary | std::ios::ate);
  const size_t bytes = (size_t)file.tellg(); file.seekg(0);
  std::vector<T> out(bytes / sizeof(T)); file.read((char*)out.data(), bytes); return out;
}}
int main(int argc, char** argv) {{
  const std::string dir = argv[1];
  std::vector<{k}> keys = load<{k}>(dir + "/keys.bin");
  std::vector<{v}> values = load<{v}>(dir + "/values.bin");
  const int n = (int)keys.size();
  {k} *d_keys; {v} *d_values; void* temp = nullptr; size_t temp_bytes = 0;
  cudaMalloc(&d_keys, n * sizeof({k}) + 1); cudaMalloc(&d_values, n * sizeof({v}) + 1);
  cudaMemcpy(d_keys, keys.data(), n * sizeof({k}), cudaMemcpyHostToDevice);
  cudaMemcpy(d_values, values.data(), n * sizeof({v}), cudaMemcpyHostToDevice);
  cub::DeviceMergeSort::{call}(temp, temp_bytes, d_keys, d_values, n, ::cuda::std::less<{k}>{{}});
  cudaMalloc(&temp, temp_bytes);
  cub::DeviceMergeSort::{call}(temp, temp_bytes, d_keys, d_values, n, ::cuda::std::less<{k}>{{}});
  if (cudaDeviceSynchronize() != cudaSuccess) {{ std::fprintf(stderr, "cub failed\\n"); return 3; }}
  cudaMemcpy(keys.data(), d_keys, n * sizeof({k}), cudaMemcpyDeviceToHost);
  cudaMemcpy(values.data(), d_values, n * sizeof({v}), cudaMemcpyDeviceToHost);
  std::ofstream(dir + "/keys_out.bin", std::ios::binary).write((const char*)keys.data(), n * sizeof({k}));
  std::ofstream(dir + "/values_out.bin", std::ios::binary).write((const char*)values.data(), n * sizeof({v}));
  return 0;
}}
'''
