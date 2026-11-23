"""What the corpus calls outside its kernels: CUDA runtime, CUB, cuBLAS, NCCL and PyTorch's CUDA API,
and for each what qpel maps it to, or that it stays unmapped.

Every source file of the corpus (corpus/*/, kernels and host code, vendored third-party headers
excluded) is read as text, comments and #include lines removed, and every identifier of those APIs
counted per project. Each identifier is classified by the first rule of RULES that matches it:

    library   a qpel library: CUB's block-level templates (frontends/include/qpel_cub) or a device-wide
              pipeline (frontends/library.py, LIBRARIES.md)
    host      stood in for by qpel's PyTorch op host (frontends/torch_op.py): the op runs on PyTorch's
              MPS device and its current stream, in order, with the package's own launch geometry
    planned   host code the host-API IR is to carry (streams, events, allocation, copies, graphs);
              the Metal column names the counterpart
    unmapped  no qpel counterpart; the Metal column names what a port would use

CUB names count as library only when qpel defines them, so a CUB helper qpel lacks shows up as
unmapped. An identifier no rule matches is 'unclassified', which tests/test_library_map.py rejects:
a new corpus source with a new API has to be classified here.

corpus/library_map.json records the result, as corpus/coverage.json records kernel coverage:

    python3 -m frontends.library_map            # print the report
    python3 -m frontends.library_map --update   # rewrite library_map.json
    python3 -m frontends.library_map --check    # fail if the result differs from library_map.json
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys

from . import library

HERE = Path(__file__).resolve().parents[1]
CORPUS = HERE/'corpus'
RESULT = CORPUS/'library_map.json'
QPEL_CUB = HERE/'frontends/include/qpel_cub'
SUFFIXES = {'.cu', '.cuh', '.cpp', '.cc', '.c', '.h', '.hpp', '.m', '.mm'}
SKIPPED = {'include', 'triton'}  # the corpus's shared stub headers; Triton kernels have no CUDA host code
STRIPPED = re.compile(r'//[^\n]*|/\*.*?\*/|^[ \t]*#[ \t]*include[^\n]*', re.S | re.M)
IDENTIFIER = re.compile(r'\b(cuda[A-Z]\w*|cublas(?:Lt)?[A-Z]\w*|nccl[A-Z]\w*|cudnn[A-Z]\w*|cusparse[A-Z]\w*|'
                        r'cufft[A-Z]\w*|curand[A-Z]\w*|thrust::\w+|cub::\w+(?:::\w+)?|'
                        r'(?:at|c10)::cuda::\w+(?:::\w+)?)\b')

HOST = 'host'
PLANNED = 'planned'
UNMAPPED = 'unmapped'
LIBRARY = 'library'

# (pattern, status, Metal counterpart, note); the first match wins
RULES = [
    # PyTorch's CUDA API in extensions: the op host runs the op on PyTorch's MPS device and stream
    (r'(at|c10)::cuda::(getCurrentCUDAStream|CUDAStream|CUDAGuard|OptionalCUDAGuard|MaybeSetDevice|device_count)',
     HOST, 'torch.mps current stream', 'one device, PyTorch\'s current stream'),
    (r'at::cuda::ATenCeilDiv', HOST, '-', 'grid arithmetic; the op host launches with the package\'s plan'),
    (r'at::cuda::cub::radix_sort_pairs', LIBRARY, 'qpel library pipeline',
     'ATen\'s wrapper of cub::DeviceRadixSort::SortPairs: the same pipeline'),
    (r'at::cuda::cub(::\w+)?', LIBRARY, 'qpel library pipelines', 'ATen\'s wrapper of device-wide CUB'),
    (r'c10::cuda::getStreamFromPool', PLANNED, 'a second MTLCommandQueue', 'concurrent streams'),
    (r'c10::cuda::CUDACachingAllocator(::\w+)?', PLANNED, 'MTLHeap', 'scratch from PyTorch\'s allocator'),
    (r'cudaStream_t|cudaSetDevice|cudaGetDevice', HOST, 'torch.mps current stream',
     'launch on the current stream of the one device'),
    (r'cudaGetLastError|cudaPeekAtLastError|cudaGetErrorString|cudaError_t|cudaSuccess|cudaCheckError',
     HOST, 'MTLCommandBuffer status', 'launch status checks; the op host has no separate error query'),
    (r'cudaFuncSetAttribute|cudaFuncAttributeMaxDynamicSharedMemorySize|cudaFuncGetAttributes|cudaFuncAttributes|'
     r'cudaFuncSetCacheConfig|cudaFuncCachePreferShared|cudaFuncSetSharedMemConfig|cudaSharedMemBankSizeFourByte',
     PLANNED, 'setThreadgroupMemoryLength, MTLComputePipelineState limits',
     'per-kernel shared memory opt-in and cache preferences'),
    (r'cudaDeviceGetAttribute|cudaDevAttr\w+', PLANNED, 'MTLDevice properties',
     'compute capability, SM count, shared memory limit'),
    (r'cudaStream\w+', PLANNED, 'MTLCommandQueue, command buffers', 'stream creation and synchronisation'),
    (r'cudaEvent\w*', PLANNED, 'MTLSharedEvent', 'ordering between streams'),
    (r'cudaDeviceSynchronize', PLANNED, 'waitUntilCompleted', 'wait for all work'),
    (r'cudaMalloc\w*|cudaFree\w*|cudaMemLocation\w*|cudaMemPrefetch\w*', PLANNED,
     'MTLDevice newBuffer, MTLHeap (shared storage for host memory)', 'allocation, asynchronous and pinned'),
    (r'cudaMemset\w*', PLANNED, 'MTLBlitCommandEncoder fillBuffer', 'clearing buffers'),
    (r'cudaMemcpy\w*|cudaGetSymbolAddress', PLANNED, 'MTLBlitCommandEncoder copy, or shared storage',
     'copies, constant memory'),
    (r'cudaGraph\w*', PLANNED, 'MTLIndirectCommandBuffer', 'captured launch graphs'),
    (r'cudaLaunch\w*', PLANNED, 'dispatchThreadgroups', 'launches through the runtime API'),
    (r'cudaGridDependencySynchronize|cudaTriggerProgrammaticLaunchCompletion', PLANNED, 'command buffer order',
     'programmatic dependent launch'),
    (r'cudaDataType_t', UNMAPPED, 'MPSDataType', 'cuBLAS operand types'),
    (r'cublasLt\w*', UNMAPPED, 'MPSGraph matrixMultiplication', 'bitsandbytes\' int8 matmul path'),
    (r'cublas\w*', UNMAPPED, 'MPSMatrixMultiplication, MPSGraph', 'GEMM and its handles'),
    (r'nccl\w*', UNMAPPED, '-', 'multi-GPU collectives; one GPU on a Mac'),
    (r'cudnn\w*', UNMAPPED, 'MPSGraph convolution', 'convolutions'),
    (r'(cusparse|cufft|curand)\w*', UNMAPPED, 'MPS, Accelerate', ''),
    (r'thrust::\w+', UNMAPPED, 'qpel library pipelines', ''),
]
RULES = [(re.compile(pattern + r'$'), status, metal, note) for pattern, status, metal, note in RULES]


def cub_defined():
    """The cub:: names qpel's CUB headers define, and the device-wide algorithms frontends/library.py expands."""
    text = '\n'.join(path.read_text() for path in QPEL_CUB.rglob('*') if path.is_file())
    names = set(re.findall(r'\b(?:struct|class|using|enum(?:\s+class)?)\s+(\w+)', text))
    names |= set(re.findall(r'^\s*(?:[\w:<>,\s]+\s)?(\w+)\s*\(', text, re.M))
    names |= {constant for block in re.findall(r'enum[^{]*\{([^}]*)\}', text) for constant in re.findall(r'\b([A-Z_]+)\b', block)}
    device = {'cub::DeviceScan::InclusiveSum', 'cub::DeviceScan::ExclusiveSum', 'cub::DeviceRadixSort::SortPairs',
              'cub::DeviceSegmentedRadixSort::SortPairs', *library.MERGE_SORTS}
    return names, device


def classify(identifier, cub=None):
    if identifier.startswith('cub::'):
        names, device = cub or cub_defined()
        parts = identifier.split('::')[1:]
        if identifier in device:
            return LIBRARY, 'qpel library pipeline', 'device-wide, expanded from a launch plan\'s library stage'
        if parts[0].startswith('Device'):
            return UNMAPPED, 'qpel library pipeline', 'a device-wide algorithm qpel has no pipeline for'
        if parts[0] in names:
            return LIBRARY, 'qpel_cub', 'block-level template, imported with the kernel'
        return UNMAPPED, '-', 'a CUB helper qpel_cub does not define'
    for pattern, status, metal, note in RULES:
        if pattern.match(identifier):
            return status, metal, note
    return 'unclassified', '', ''


def sources():
    for project in sorted(path for path in CORPUS.iterdir() if path.is_dir() and path.name not in SKIPPED):
        for path in sorted(project.rglob('*')):
            if path.is_file() and path.suffix in SUFFIXES and 'third_party' not in path.parts:
                yield project.name, path


def survey():
    """{identifier: {project: count}} over the corpus."""
    counts = defaultdict(lambda: defaultdict(int))
    for project, path in sources():
        for match in IDENTIFIER.finditer(STRIPPED.sub('', path.read_text(errors='replace'))):
            counts[match.group(1)][project] += 1
    return counts


def result():
    cub = cub_defined()
    out = {}
    for identifier, projects in sorted(survey().items()):
        status, metal, note = classify(identifier, cub)
        out[identifier] = {'status': status, 'metal': metal, 'note': note, 'uses': dict(sorted(projects.items()))}
    return out


def report(entries):
    lines = []
    totals = defaultdict(int)
    for entry in entries.values():
        totals[entry['status']] += sum(entry['uses'].values())
    lines.append('uses by status: ' + ', '.join(f'{status} {count}' for status, count in
                                                 sorted(totals.items(), key=lambda item: -item[1])))
    for status in (LIBRARY, HOST, PLANNED, UNMAPPED, 'unclassified'):
        rows = [(name, entry) for name, entry in entries.items() if entry['status'] == status]
        if not rows:
            continue
        lines.append(f'\n{status}:')
        for name, entry in sorted(rows, key=lambda row: -sum(row[1]['uses'].values())):
            uses = ', '.join(f'{project} {count}' for project, count in entry['uses'].items())
            lines.append(f'  {name:50} {uses:34} -> {entry["metal"]}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--update', action='store_true', help='rewrite corpus/library_map.json')
    parser.add_argument('--check', action='store_true', help='fail when the result differs from library_map.json')
    args = parser.parse_args(argv)
    entries = result()
    if args.update:
        RESULT.write_text(json.dumps(entries, indent=1) + '\n')
    if args.check:
        recorded = json.loads(RESULT.read_text())
        if recorded != entries:
            changed = sorted(set(recorded) ^ set(entries) | {name for name in set(recorded) & set(entries)
                                                             if recorded[name] != entries[name]})
            print('library_map.json differs (run --update): ' + ', '.join(changed[:20]), file=sys.stderr)
            return 1
        return 0
    print(report(entries))
    return 0


if __name__ == '__main__':
    sys.exit(main())
