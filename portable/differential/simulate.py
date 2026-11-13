"""Run CUDA sources on the CPU simulator (include/cuda_runtime.h) with given data.

Both an original source and qpel's generated CUDA are driven from the same qpel
launch plan, so their outputs can be compared buffer by buffer:

- generated CUDA: each stage calls its kernel with the stage's bindings, in
  parameter order (a shape is passed as QpelShape, a value as itself);
- an original: each stage calls the original kernel with the arguments the
  importer recorded (report['arguments']), so a count receives W*H*N and scalar
  extents receive the shape's members.

One program is compiled per source and run once per case. Everything that varies
between cases (sizes, scalar values, launch geometry) is read from files.
"""
import ast
import operator
import os
import shutil
from pathlib import Path
import struct
import subprocess
import tempfile

import sys

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from qpel_types import buffer_length, NUMPY_DTYPES, element, is_buffer, is_struct, normalize_plan, pack_struct, term  # noqa: E402

INCLUDE = HERE/'include'
IMPORT_INCLUDE = HERE.parent/'frontends/include/libraries'  # PyTorch, cuBLAS; CUDA is the simulator's own


class _Types(dict):
    """type (term or concept name) -> numpy dtype of a buffer element or value."""
    def __missing__(self, key):
        t = term(key)
        return np.dtype(NUMPY_DTYPES[element(t) if is_buffer(t) else t]).type


class _Values(dict):
    """value type (term or concept name) -> its term; the simulator passes 32-bit words."""
    def __missing__(self, key):
        t = term(key)
        if t not in C_VALUE and not is_struct(t):
            raise ValueError(f'the simulator has no scalar arguments of type {t}')
        return t


DTYPES = _Types()
VALUE_TYPES = _Values()
C_VALUE = {'u32': 'qpel_sim_u32', 'i32': 'qpel_sim_i32', 'f32': 'qpel_sim_f32', 'u64': 'qpel_sim_u64', 'i64': 'qpel_sim_i64',
           'f16': 'qpel_sim_f16'}
WIDE = {'u64', 'i64'}  # passed as two 32-bit words, low word first


class Case:
    """One set of test data: shapes {name: (W, H, N)}, inputs {name: array}, values {name: number}."""

    def __init__(self, shapes, inputs=None, values=None, label='', defined=None):
        """defined: {output: number of leading elements the kernel writes}, when it leaves the rest
        undefined (a grid-stride loop up to n); outputs not listed are compared in full."""
        self.shapes, self.inputs, self.values, self.label = shapes, inputs or {}, values or {}, label
        self.defined = defined or {}


def volume(shape):
    return shape[0]*shape[1]*shape[2]


def evaluate(expression, names):
    """An integer expression of W, H and N (+ - * / with C's truncating division)."""
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: lambda a, b: a // b,
           ast.FloorDiv: lambda a, b: a // b}

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](walk(node.left), walk(node.right))
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        raise ValueError('Unsupported grid expression: ' + expression)
    return walk(ast.parse(expression, mode='eval'))


def geometry(stage, shape):
    """(grid, block) the way qpel's CUDA host launches a stage over its shape."""
    if stage.get('domain') == 'groups':
        from qpel_schema import group_count
        return tuple(group_count(g, shape) for g in stage['groups']), tuple(stage['blockShape'])
    if stage.get('domain') == 'grid3d':
        block = tuple(stage['blockShape'])
        return tuple(-(-shape[a] // block[a]) for a in range(3)), block
    block = stage['blockSize']
    return (-(-volume(shape) // block), 1, 1), (block, 1, 1)


class Program:
    """A compiled simulator program for one source and one launch plan."""

    def __init__(self, source, plan, work, original=None, include=(), grids=None):
        """original: the import report when `source` is the original (not qpel-generated) source.
        grids: {kernel: [x, y, z]} grid expressions transcribed from the original host code."""
        plan = normalize_plan(plan)
        self.plan, self.work, self.original, self.grids = plan, Path(work), original, grids or {}
        self.resources = {r['name']: r for r in plan['resources']}
        self.scalars = []  # names of the 32-bit words in scalars.bin, in order
        source = Path(source).resolve()
        include = [Path(folder).resolve() for folder in include]
        bodies = {Path(f).resolve(): spans for f, spans in (original or {}).get('header_host_bodies', {}).items()}
        if bodies:
            # Library headers with host code: simulate a copy of their tree with those bodies
            # replaced by ';' (declarations stay); quoted includes still resolve inside the copy.
            root = Path(os.path.commonpath([source.parent] + [f.parent for f in bodies]))
            tree = self.work/'tree'
            # Never copy a filesystem root or a tree holding the copy itself: a system header
            # mistaken for a library header once made this copy itself until the disk was full.
            if root == Path(root.anchor) or tree.resolve().is_relative_to(root.resolve()):
                raise RuntimeError(f'{source.name}: refusing to copy {root} for the simulator; its library '
                                   f'headers are {sorted(str(f) for f in bodies)[:3]}')
            shutil.copytree(root, tree, dirs_exist_ok=True)
            for f, spans in bodies.items():
                copy = tree/f.relative_to(root)
                text = bytearray(copy.read_bytes())
                for start, end in sorted(spans, reverse=True):
                    text[start:end] = b';' + bytes(b if b == 10 else 32 for b in text[start + 1:end])
                copy.write_bytes(bytes(text))
            include = [tree/d.relative_to(root) for d in include if d.is_relative_to(root)] + include
            source = tree/source.relative_to(root)
        if original and original.get('host_spans'):
            text = bytearray(source.read_bytes())
            for start, end in original['host_spans']:  # blank host code, keeping line numbers
                text[start:end] = bytes(b if b == 10 else 32 for b in text[start:end])
            # The copy lives in the work folder (never next to the source: parallel runs and the
            # repository); its quoted includes resolve through the source's own folder.
            device = self.work/('device_' + source.name)
            device.write_bytes(bytes(text))
            include = [source.parent] + include
            source = device
        self.source, self.include = source, include
        self.main = self.work/self.MAIN
        self.main.write_text(self.program(source))
        self.binary = self.work/'simulate'
        self.build()

    MAIN = 'main.cpp'

    def build(self):
        build = subprocess.run(['clang++', '-std=c++17', '-O1', '-ffp-contract=off', '-fsanitize=undefined',
                                '-fno-sanitize-recover=all', '-w', '-I', str(INCLUDE),
                                *[arg for folder in self.include for arg in ('-I', str(folder))],
                                # The importer's library stand-ins (PyTorch, ATen, c10, pybind11, cuBLAS).
                                '-I', str(IMPORT_INCLUDE),
                                str(self.main), '-o', str(self.binary)], capture_output=True, text=True, timeout=180)
        if build.returncode:
            raise RuntimeError(f'{self.source.name} does not compile for the simulator:\n{build.stderr[-4000:]}')

    def word(self, name):
        if name not in self.scalars:
            self.scalars.append(name)
        return f'S[{self.scalars.index(name)}]'

    HEADERS = ('"cuda_runtime.h"', '"cuda_fp16.h"', '"cuda_bf16.h"')

    def program(self, source):
        lines = [f'#include {h}' for h in self.HEADERS] + [f'#include "{source}"', '#include <vector>', '#include <string>',
                 '#include <cstring>', '#include <fstream>', '#include <sys/mman.h>', '#include <unistd.h>', '#include <cstdlib>',
                 'struct Ptr { void* p; template <class T> operator T*() const { return (T*)p; } };',
                 'static std::vector<char> load(const std::string& path) {',
                 '  std::ifstream f(path, std::ios::binary); return std::vector<char>((std::istreambuf_iterator<char>(f)), {}); }',
                 # A buffer ends where an inaccessible page begins, so an access past its end stops the
                 # simulator (SIGSEGV) instead of reading or changing other memory. Its start keeps the
                 # alignment of its elements: the size is a multiple of the element size.
                 'struct Guarded { char* p; size_t n; char* data() { return p; } size_t size() const { return n; } };',
                 'static Guarded guarded(const std::vector<char>& v) {',
                 '  size_t page = (size_t)sysconf(_SC_PAGESIZE), span = (v.size() + page - 1) / page * page;',
                 '  char* base = (char*)mmap(nullptr, span + page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);',
                 '  if (base == (char*)MAP_FAILED) std::abort();',
                 '  mprotect(base + span, page, PROT_NONE);',
                 '  char* p = base + span - v.size(); std::memcpy(p, v.data(), v.size()); return Guarded{p, v.size()}; }',
                 'static unsigned int qpel_sim_u32(unsigned int w) { return w; }',
                 'static int qpel_sim_i32(unsigned int w) { int v; std::memcpy(&v, &w, 4); return v; }',
                 'static float qpel_sim_f32(unsigned int w) { float v; std::memcpy(&v, &w, 4); return v; }',
                 'static unsigned long long qpel_sim_u64(unsigned int lo, unsigned int hi) { return (unsigned long long)hi << 32 | lo; }',
                 'static long long qpel_sim_i64(unsigned int lo, unsigned int hi) { return (long long)qpel_sim_u64(lo, hi); }',
                 'static __half qpel_sim_f16(unsigned int w) { unsigned short b = (unsigned short)w; __half v; std::memcpy(&v, &b, 2); return v; }']
        namespaces = sorted({q.rpartition('::')[0] for q in (self.original or {}).get('qualified', {}).values()} - {''})
        lines += [f'using namespace {n};' for n in namespaces]
        lines += ['int main(int argc, char** argv) {', '  std::string dir = argv[1];',
                  '  std::vector<char> scalars = load(dir + "/scalars.bin");',
                  '  const unsigned int* S = (const unsigned int*)scalars.data();']
        data = [r for r in self.plan['resources'] if r['kind'] not in ('shape', 'value')]
        for r in self.plan['resources']:
            if r['kind'] == 'value' and is_struct(r['type']):
                lines.append(f'  {r["type"][7:]} qpel_struct_{r["name"]}; {{ std::vector<char> b = load(dir + "/value_{r["name"]}.bin"); '
                             f'std::memcpy(&qpel_struct_{r["name"]}, b.data(), sizeof qpel_struct_{r["name"]}); }}')
        for r in data:
            lines.append(f'  Guarded b_{r["name"]} = guarded(load(dir + "/{r["name"]}.bin"));')
        lines += self.upload(data)
        for s in self.plan['stages']:
            lines += self.stage(s)
        lines += self.download(data)
        for r in data:
            lines.append(f'  {{ std::ofstream f(dir + "/{r["name"]}.out", std::ios::binary); '
                         f'f.write(b_{r["name"]}.data(), b_{r["name"]}.size()); }}')
        lines += ['  return 0;', '}']
        return '\n'.join(lines) + '\n'

    def stage(self, s):
        params = {b['parameter']: b['resource'] for b in s['bindings']}
        kernel = s['kernel']

        def resource_arg(name, wanted=None):
            r = self.resources[name]
            if r['kind'] == 'shape':
                dims = [self.word(f'{name}.{a}') for a in 'xyz']
                if wanted == 'volume':
                    return f'({dims[0]} * {dims[1]} * {dims[2]})'
                return 'QpelShape{' + ', '.join(dims) + ', 0u}' if not self.original else 'uint3{' + ', '.join(dims) + '}'
            if r['kind'] == 'value' and is_struct(r['type']):
                return f'qpel_struct_{name}'  # loaded from value_<name>.bin before the stages
            if r['kind'] == 'value':
                kind = VALUE_TYPES[r['type']]
                words = [self.word('value.' + name)] + ([self.word('value.' + name + '.hi')] if kind in WIDE else [])
                return f'{C_VALUE[kind]}({", ".join(words)})'
            return self.buffer(name)
        if self.original:
            args = []
            for a in self.original['arguments'][kernel]:
                if a['source'] == 'extent':
                    args.append(self.word(f'{params[a["parameter"]]}.{a["axis"]}'))
                elif a['source'] == 'volume':
                    args.append(resource_arg(params[a['parameter']], 'volume'))
                elif a['source'] == 'parameter':
                    args.append(resource_arg(params[a['parameter']]))
                else:
                    raise ValueError(f'{kernel}: argument {a["name"]} has no qpel equivalent')
            callee = self.original['qualified'][kernel]
        else:
            args = [resource_arg(params[b['parameter']]) for b in s['bindings']]
            callee = kernel
        g = [self.word(f'grid.{kernel}.{a}') for a in 'xyz']
        b = [self.word(f'block.{kernel}.{a}') for a in 'xyz']
        return self.launch(kernel, callee, args, g, b)

    # How buffers reach the kernels and how a launch runs: overridden for real CUDA (gpgpusim.py).
    def upload(self, data):
        return []

    def download(self, data):
        return []

    def buffer(self, name):
        return f'Ptr{{b_{name}.data()}}'

    def launch(self, kernel, callee, args, g, b):
        return [f'  gridDim = dim3({", ".join(g)}); blockDim = dim3({", ".join(b)});',
                '  for (unsigned bz = 0; bz < gridDim.z; ++bz) for (unsigned by = 0; by < gridDim.y; ++by)'
                ' for (unsigned bx = 0; bx < gridDim.x; ++bx)',
                '  for (unsigned tz = 0; tz < blockDim.z; ++tz) for (unsigned ty = 0; ty < blockDim.y; ++ty)'
                ' for (unsigned tx = 0; tx < blockDim.x; ++tx) {',
                '    blockIdx = uint3{bx, by, bz}; threadIdx = uint3{tx, ty, tz};',
                f'    {callee}({", ".join(args)});', '  }']

    def run(self, case):
        """{resource name: output array} for every data resource after all stages."""
        with tempfile.TemporaryDirectory(dir=self.work) as folder:
            folder = Path(folder)
            self.write_case(case, folder)
            result = subprocess.run([str(self.binary), str(folder)], capture_output=True, text=True, timeout=300)
            if result.returncode in (-10, -11) or 'DEADLYSIGNAL' in result.stderr:  # SIGBUS/SIGSEGV at a guard page
                raise RuntimeError(f'simulator failed on {case.label}: an access past the end of a buffer, or another invalid address')
            if result.returncode:
                raise RuntimeError(f'simulator failed on {case.label}: {result.stderr[-3000:]}')
            return self.read_outputs(folder)

    def write_case(self, case, folder):
        """Write a case's scalars and input buffers into `folder`, where the program reads them."""
        words = {}
        for name, shape in case.shapes.items():
            for a, v in zip('xyz', shape):
                words[f'{name}.{a}'] = v
        for name, value in case.values.items():
            if is_struct(self.resources[name]['type']):
                fields = self.plan['structs'][self.resources[name]['type'][7:]]
                (folder/f'value_{name}.bin').write_bytes(pack_struct(fields, value))
                continue
            kind = VALUE_TYPES[self.resources[name]['type']]
            if kind in WIDE:
                bits = value % 2**64
                words['value.' + name], words['value.' + name + '.hi'] = bits % 2**32, bits >> 32
            elif kind == 'f16':
                words['value.' + name] = struct.unpack('<H', struct.pack('<e', value))[0]
            else:
                words['value.' + name] = struct.unpack('<I', struct.pack('<f' if kind == 'f32' else '<i' if kind == 'i32' else '<I', value))[0]
        for s in self.plan['stages']:
            shape = case.shapes[s['grid']]
            grid, block = geometry(s, shape)
            if self.original and s['kernel'] in self.grids:
                names = dict(zip('WHN', shape))
                grid = tuple(evaluate(e, names) for e in self.grids[s['kernel']])
            for a, gv, bv in zip('xyz', grid, block):
                words[f'grid.{s["kernel"]}.{a}'], words[f'block.{s["kernel"]}.{a}'] = gv, bv
        (folder/'scalars.bin').write_bytes(struct.pack(f'<{len(self.scalars)}I', *(words[n] for n in self.scalars)))
        for r in self.plan['resources']:
            if r['kind'] in ('shape', 'value'):
                continue
            count = buffer_length(r, case.shapes)
            if r['kind'] in ('input', 'inout'):
                array = np.ascontiguousarray(case.inputs[r['name']], dtype=DTYPES[r['type']]).reshape(-1)
                if array.size != count:
                    raise ValueError(f'{r["name"]} has {array.size} elements, not {count}')
            else:
                array = np.zeros(count, DTYPES[r['type']])
            (folder/(r['name'] + '.bin')).write_bytes(array.tobytes())

    def read_outputs(self, folder):
        outputs = {}
        for r in self.plan['resources']:
            if r['kind'] not in ('shape', 'value'):
                outputs[r['name']] = np.frombuffer((folder/(r['name'] + '.out')).read_bytes(), DTYPES[r['type']]).copy()
        return outputs
