"""Run a Metal source on the CPU simulator instead of the Apple GPU.

A Metal kernel that never ends cannot be stopped on an Apple GPU: killing the process leaves
the command buffer running, and repeated hangs can restart the machine. That happens to ports
with probe or retry loops (hash tables, compare-and-swap loops) given a wrong size. On the CPU
the same kernel is a process with a timeout, so `qpel verify --metal cpu` (or `preflight`) runs
an unproven port there first.

The source is pasted into namespace qpel_metal after include/metal_cpu.h (Metal's types,
atomics and math in C++). Each kernel gets a CUDA-style wrapper that takes its non-builtin
arguments in [[buffer(n)]] order (references as values), computes the builtins it asks for
(thread_position_in_grid and the rest) from blockIdx and threadIdx, and skips the threads that
Metal's dispatchThreads would not launch. The wrappers then run in the CUDA simulator
(simulate.Program) exactly as an original CUDA kernel does.
"""
from pathlib import Path
import re

from frontends import clang_ast
from frontends.c_import import attributes, params_of

from .simulate import Program, volume

# Metal builtin attribute -> its value from the simulator's CUDA built-ins, as a (x, y, z) triple.
# `qpel_threads` is the thread count Metal dispatches on each axis (dispatchThreads).
BUILTINS = {
    'MetalThreadPosGridAttr': ('blockIdx.x * blockDim.x + threadIdx.x', 'blockIdx.y * blockDim.y + threadIdx.y',
                               'blockIdx.z * blockDim.z + threadIdx.z'),
    'MetalThreadGroupPosGridAttr': ('blockIdx.x', 'blockIdx.y', 'blockIdx.z'),
    'MetalThreadPosGroupAttr': ('threadIdx.x', 'threadIdx.y', 'threadIdx.z'),
    # A partial threadgroup at the end of the grid is smaller (ggml's kernels index with it).
    'MetalThreadsPerGroupAttr': tuple(f'qpel_metal_min(blockDim.{a}, qpel_threads.{a} - blockIdx.{a} * blockDim.{a})'
                                      for a in 'xyz'),
    'MetalThreadGroupsPerGridAttr': ('gridDim.x', 'gridDim.y', 'gridDim.z'),
    'MetalThreadsPerGridAttr': ('qpel_threads.x', 'qpel_threads.y', 'qpel_threads.z'),
    'MetalThreadIndexGroupAttr': ('(threadIdx.z * blockDim.y + threadIdx.y) * blockDim.x + threadIdx.x', '0', '0'),
    'MetalThreadIndexSIMDGroupAttr': ('((threadIdx.z * blockDim.y + threadIdx.y) * blockDim.x + threadIdx.x) % 32', '0', '0'),
    'MetalSIMDGroupIndexGroupAttr': ('((threadIdx.z * blockDim.y + threadIdx.y) * blockDim.x + threadIdx.x) / 32', '0', '0'),
    'MetalThreadsPerSIMDGroupAttr': ('32', '0', '0'),
}
UNLAUNCHED = ' || '.join(f'blockIdx.{a} * blockDim.{a} + threadIdx.{a} >= qpel_threads.{a}' for a in 'xyz')


def builtin_value(attribute, qual_type):
    parts = BUILTINS[attribute]
    t = qual_type.replace('const', '').strip()
    width = int(t[-1]) if t[-1] in '234' else 1
    return f'{t}{{{", ".join(parts[:width])}}}' if width > 1 else f'({t})({parts[0]})'


def local_threadgroup(text):
    """`threadgroup T x[n];` declared in a function body -> one static copy (blocks run one at a time).
    qpel's own Metal declares group memory through macros (backends/metal.h: QPEL_SHARED, the
    barrier counter, the workgroup-sum scratch), whose definitions get the same."""
    text = re.sub(r'(?m)^(\s*)threadgroup\s+(?=[\w:<>]+\s+\w+\s*(\[[^]]*\]\s*)*;)', r'\1static ', text)
    return re.sub(r'(?m)^(#define QPEL_(?:SHARED\(T, name, n\)|BARRIER_COUNT_SCRATCH|GRID_LOCAL\(name\)) )threadgroup ',
                  r'\1static ', text)


def instantiations(unit):
    """{name: (template's kernel declaration, {type parameter: argument}, C++ name)} for each
    explicit instantiation of a kernel template, by its [[host_name]] (as the importer names
    kernels) and by its C++ spelling, `kernel_conv_transpose_2d<float>`."""
    found = {}
    for template in unit.get('inner', ()):
        if template.get('kind') != 'FunctionTemplateDecl':
            continue
        inner = template.get('inner', ())
        pattern = next((d for d in inner if d.get('kind') == 'FunctionDecl' and 'MetalKernelAttr' in attributes(d)
                        and not any(c['kind'] == 'TemplateArgument' for c in d.get('inner', ()))), None)
        names = [d.get('name') for d in inner if d.get('kind') == 'TemplateTypeParmDecl']
        if pattern is None:
            continue
        for spec in inner:
            arguments = [c for c in spec.get('inner', ()) if c['kind'] == 'TemplateArgument']
            if spec.get('kind') != 'FunctionDecl' or not arguments or not all('type' in a for a in arguments):
                continue  # (an argument that is not a type keeps the pattern from running on the CPU)
            given = [a['type']['qualType'] for a in arguments]
            if len(given) != len(names):
                continue
            entry = (pattern, dict(zip(names, given)), f'{template["name"]}<{", ".join(given)}>')
            found[entry[2]] = entry
            host = host_name(spec)
            if host:
                found[host] = entry
    return found


def host_name(decl):
    """The [[host_name("...")]] of a declaration, read from its file (clang's JSON leaves it out)."""
    for c in decl.get('inner', ()):
        begin = c.get('range', {}).get('begin', {})
        if c['kind'] == 'MetalHostNameAttr' and begin.get('_file'):
            text = Path(begin['_file']).read_bytes()[begin['offset']:begin['offset'] + 300].decode('utf-8', 'replace')
            m = re.match(r'host_name\s*\(\s*"([^"]+)"', text)
            if m:
                return m.group(1)
    return None


def cuda_source(path, report, include, text, work):
    """Write work/metal_on_cpu.cu, the Metal text with wrappers; return (path, report for the
    simulator). `text` is the Metal source as it would be compiled (includes inlined, function
    constants given their values)."""
    unit, _ = clang_ast.parse(path, 'metal', include)
    kernels = {d['name']: d for d in unit.get('inner', ()) if 'MetalKernelAttr' in attributes(d)}
    for d in unit.get('inner', ()):  # kernels inside namespaces, one level down
        if d.get('kind') == 'NamespaceDecl':
            kernels.update({f'{d["name"]}::{k["name"]}': k for k in d.get('inner', ()) if 'MetalKernelAttr' in attributes(k)})
    records = sorted({d['name'] for d in unit.get('inner', ())
                      if d.get('kind') == 'CXXRecordDecl' and d.get('name') and d.get('completeDefinition')})
    body = re.sub(r'(?m)^\s*#\s*include\s*<metal_stdlib>.*$', '', text)
    # C++ allows no attribute on an explicit instantiation; the wrappers call it by its C++ name.
    body = re.sub(r'\[\[\s*host_name\s*\(\s*"[^"]*"\s*\)\s*\]\]', '', body)
    lines = ['// Generated by qpel (differential/metal_cpu.py): a Metal source for the CPU simulator.',
             '#include "metal_cpu.h"',
             'inline uint3 qpel_threads;  // the threads Metal dispatches on each axis',
             'inline unsigned qpel_metal_min(unsigned a, unsigned b) { return a < b ? a : b; }',
             'namespace qpel_metal {', local_threadgroup(body), '']
    templates = instantiations(unit)
    wrappers, arguments = {}, {}
    sized = report.get('shared') or {}  # threadgroup parameters sized_threadgroups made arrays
    for kernel, qualified in report['qualified'].items():
        decl, substitute, callee = kernels.get(qualified), {}, qualified
        if decl is None and qualified in templates:
            decl, substitute, callee = templates[qualified]
        if decl is None:
            raise ValueError(f'{Path(path).name}: kernel {qualified} is not at namespace scope, so it cannot run on the CPU')
        params = [p for p in params_of(decl) if not ('MetalLocalIndexAttr' in attributes(p) and p['name'] in sized)]
        by_name = {a['name']: a for a in report['arguments'][kernel]}
        passed, call, locals_ = [], [], []
        for p in params:
            attrs = attributes(p)
            builtin = next((k for k in attrs if k.startswith('Metal') and k != 'MetalBufferIndexAttr'), None)
            qual = p['type']['qualType']
            for name, value in substitute.items():  # device const T * -> device const float *
                qual = re.sub(r'\b' + re.escape(name) + r'\b', value, qual)
            if builtin:
                if builtin not in BUILTINS:
                    raise ValueError(f'{qualified}: the builtin {builtin} has no CPU equivalent')
                call.append(builtin_value(builtin, qual))
            else:
                if qual.endswith('&'):  # constant T& arrives by value; the kernel binds its reference to the copy
                    qual = re.sub(r'\bconst\b', '', qual.rstrip(' &')).strip()
                if re.fullmatch(r'(constant\s+)?uint3', qual.strip()):
                    # a shape: the simulator passes its own uint3, which converts to Metal's here
                    name = p['name']
                    passed.append((by_name[name]['binding'], '::uint3', name))
                    locals_.append(f'  uint3 {name}_metal{{{name}.x, {name}.y, {name}.z}};')
                    call.append(f'{name}_metal')
                    continue
                passed.append((by_name[p['name']]['binding'], qual, p['name']))
                call.append(p['name'])
        passed.sort()
        wrapper = 'qpel_cpu_' + qualified.replace('::', '_')
        lines += [f'__global__ void {wrapper}({", ".join(f"{t} {n}" for _, t, n in passed)}) {{',
                  f'  if ({UNLAUNCHED}) return;  // dispatchThreads launches no such thread', *locals_,
                  f'  {callee}({", ".join(call)});', '}']
        wrappers[kernel] = 'qpel_metal::' + wrapper
        arguments[kernel] = sorted(report['arguments'][kernel], key=lambda a: a['binding'])
    lines += ['}  // namespace qpel_metal']
    lines += [f'using qpel_metal::{r};' for r in records]  # the simulator declares struct arguments by name
    lines += ['#undef kernel', '#undef device', '#undef constant', '#undef thread', '#undef threadgroup']
    out = Path(work)/'metal_on_cpu.cu'
    out.write_text('\n'.join(lines) + '\n')
    simulated = dict(report, dialect='cuda', qualified=wrappers, arguments=arguments, host_spans=[], shared={},
                     header_host_bodies={})
    return out, simulated


class MetalProgram(Program):
    """simulate.Program for a Metal source: its kernels run through the wrappers, over the
    threads Metal would dispatch (an exact count, or whole groups with whole_groups)."""

    def __init__(self, source, plan, report, work, include=(), text=None, whole_groups=False, timeout=60):
        self.whole_groups, self.timeout = whole_groups, timeout
        Path(work).mkdir(parents=True, exist_ok=True)
        cu, simulated = cuda_source(source, report, include, text, work)
        super().__init__(cu, plan, work, original=simulated, include=include)

    def program(self, source):
        # No `using namespace qpel_metal;`: its vector types would hide the simulator's uint3.
        return '\n'.join(line for line in super().program(source).splitlines()
                         if line != 'using namespace qpel_metal;') + '\n'

    def stage(self, s):
        """Set qpel_threads before the launch: the thread count on each axis."""
        names = [self.word(f'{s["grid"]}.{a}') for a in 'xyz']
        g = [self.word(f'grid.{s["kernel"]}.{a}') for a in 'xyz']
        b = [self.word(f'block.{s["kernel"]}.{a}') for a in 'xyz']
        if self.whole_groups or s.get('domain') == 'groups':
            threads = [f'{gv} * {bv}' for gv, bv in zip(g, b)]
        elif s.get('domain') == 'grid3d':
            threads = names
        else:
            threads = [' * '.join(names), '1u', '1u']
        return [f'  qpel_threads = uint3{{{", ".join(threads)}}};'] + super().stage(s)


def program(source, plan, report, work, include=(), constants=None, whole_groups=False, timeout=60):
    from .metal_text import inline_includes, instantiating_only, specialise
    needed = {report['qualified'][s['kernel']] for s in plan['stages']}
    text = instantiating_only(specialise(inline_includes(source, include), constants, report.get('shared')), needed)
    return MetalProgram(source, plan, report, work, include, text, whole_groups, timeout)


__all__ = ['program', 'MetalProgram', 'volume']
