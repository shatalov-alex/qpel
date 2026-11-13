"""PyTorch operations from a built qpel package and the original extension's launch code.

A CUDA PyTorch extension launches its kernels from host code: `kernel<<<grid, block>>>(
x.size(0), x.data_ptr<int>(), codes.data_ptr<int>())`. For every such launch of a kernel in
the package, this writes a Python function, in `torch_ops.py` next to the package's Metal
host, that takes the same tensors (and the host function's scalars) and does what the launch
does, on the Apple GPU: it reads the input tensors, runs the kernel, and writes the output
and in-place tensors. It is the launch, not the whole host function: the caller allocates
the outputs, as the host function does before its launch.

Each launch argument must be a tensor's storage (`t.data_ptr<T>()`), its length (`t.size(0)`,
`t.numel()`), a host function parameter or a literal. Anything else, and packages with more
than one dispatch, are reported and get no function.
"""
from pathlib import Path
import json
import keyword
import re

from . import clang_ast
from .c_import import LOOSE, launch_tensor, walk

FUNCTIONS = ('FunctionDecl', 'CXXMethodDecl')


class Launch:
    """One `kernel<<<...>>>(...)` in host code: where it is, the host function around it, its arguments."""

    def __init__(self, written, arguments, function, parameters, where):
        self.written, self.arguments, self.function, self.parameters, self.where = \
            written, arguments, function, parameters, where
        self.variants = [arguments]


def _callee_name(node):
    callee = node['inner'][0]
    while callee.get('kind') in LOOSE and callee.get('inner'):
        callee = callee['inner'][0]
    if callee.get('kind') == 'UnresolvedLookupExpr':
        return callee.get('name')
    if callee.get('kind') == 'DeclRefExpr':
        return callee.get('referencedDecl', {}).get('name')
    return None


def launches(path, include=()):
    """Every kernel launch in a CUDA host source, once each (template instantiations repeat them)."""
    unit, _ = clang_ast.parse(Path(path), 'cuda', tuple(include))
    found, seen = [], {}

    def visit(node, function):
        if node.get('kind') in FUNCTIONS and node.get('inner'):
            function = node
        if node.get('kind') == 'CUDAKernelCallExpr' and node.get('inner'):
            where = clang_ast.position(node)
            name = _callee_name(node)
            if name and function is not None:
                parameters = [p.get('name') for p in function.get('inner', ()) if p.get('kind') == 'ParmVarDecl']
                if where in seen:  # a template instantiation: its arguments are resolved, the pattern's may not be
                    seen[where].variants.append(node['inner'][2:])
                else:
                    seen[where] = Launch(name, node['inner'][2:], function.get('name'), parameters, where)
                    found.append(seen[where])
        for child in node.get('inner', ()):
            if child:
                visit(child, function)

    visit(unit, None)
    return found


def argument(node, parameters):
    """('data'|'size', tensor), ('parameter', name), ('literal', text) or None for one launch argument."""
    use = launch_tensor(_without_contiguous(node))
    if use:
        return use
    while node.get('kind') in LOOSE and node.get('inner'):
        node = node['inner'][0]
    if node.get('kind') == 'DeclRefExpr' and node.get('referencedDecl', {}).get('name') in parameters:
        return ('parameter', node['referencedDecl']['name'])
    if node.get('kind') in ('IntegerLiteral', 'FloatingLiteral'):
        return ('literal', node.get('value'))
    return None


def _without_contiguous(node):
    """The argument with `t.contiguous().data_ptr()` read as `t.data_ptr()`: the generated
    operation makes its inputs contiguous itself."""
    def strip(n):
        if not isinstance(n, dict):
            return n
        if n.get('kind') == 'CXXMemberCallExpr' and n.get('inner') and \
                all(a.get('kind') == 'CXXDefaultArgExpr' for a in n['inner'][1:]):  # contiguous(format = default)
            callee = n['inner'][0]
            if callee.get('kind') == 'MemberExpr' and callee.get('name') == 'contiguous' and callee.get('inner'):
                return strip(callee['inner'][0])
        if n.get('inner'):
            return {**n, 'inner': [strip(c) for c in n['inner']]}
        return n
    return strip(node)


def kernel_parameters(program, kernel):
    """The kernel's parameter names in binding order, from the package's program.qpel.json."""
    nodes = {n['id']: n for n in program['nodes']}
    for n in program['nodes']:
        if n['kind'] == 'Function' and n['attributes'].get('isKernel') and n['attributes']['name'] == kernel:
            params = [nodes[c] for c in n['children'].get('parameters', ())]
            return [p['attributes']['name'] for p in sorted(params, key=lambda p: p['attributes']['binding'])]
    raise ValueError(f'No kernel {kernel} in the program')


def _matches(kernel, written):
    """A launch of `written` launches qpel kernel `kernel` (templates import as name_T1_T2)."""
    return kernel == written or kernel.startswith(written + '_')


def _identifier(name):
    name = re.sub(r'\W', '_', name)
    return name + '_' if keyword.iskeyword(name) else name


PRELUDE = '''# Generated by qpel torch-op from a qpel package and the original extension's launches.
# Edit the program or the generator, not this file.
import importlib.util
from functools import lru_cache
from pathlib import Path
import torch


@lru_cache(maxsize=1)
def _host():
    spec = importlib.util.spec_from_file_location("qpel_metal_host", Path(__file__).with_name("metal_host.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    return host


def _tensor(t, name):
    if not isinstance(t, torch.Tensor):
        raise TypeError(name + " must be a tensor")
    if t.device.type != "mps":
        raise ValueError(name + " must be on the mps device")
    return t


def _flat(t, name, dtype):
    """The tensor's elements as the Metal kernel reads them (the same bits, as data_ptr<T>() passes them)."""
    t = _tensor(t, name).contiguous().view(-1)
    if t.element_size() != dtype.itemsize:
        raise ValueError(f"{name} has {t.element_size()}-byte elements; the kernel reads {dtype.itemsize}-byte ones")
    return t.view(dtype)


def _store(t, name, result):
    """Write a result into the caller's tensor, as the kernel writes through data_ptr<T>()."""
    _tensor(t, name)
    if not t.is_contiguous():
        raise ValueError(name + " must be contiguous: the kernel writes it in place")
    t.view(-1).view(result.dtype).copy_(result)


def _length(n, name):
    if type(n) is not int or n <= 0:
        raise ValueError(name + " must be a positive int")
    return (n, 1, 1)

'''


def _generate_one(launch, plan, kernel, parameters):
    """(function name, source) or raises ValueError with the reason."""
    if len(launch.arguments) != len(parameters):
        raise ValueError(f'{len(launch.arguments)} arguments for {len(parameters)} parameters')
    resources = {r['name']: r for r in plan['resources']}
    binding = {b['parameter']: b['resource'] for b in plan['stages'][0]['bindings']}
    tensors, scalars = [], []  # Python parameters, in order of first appearance
    inputs, shapes, values, stores = {}, {}, {}, []
    tensor_of = {}  # resource -> tensor holding it

    def want(kind_list, name):
        if name not in kind_list:
            kind_list.append(name)

    for param, node in zip(parameters, launch.arguments):
        use = argument(node, launch.parameters)
        resource = resources[binding[param]]
        kind = resource['kind']
        if use is None:
            raise ValueError(f'argument for {param} is neither a tensor, its length, a parameter nor a literal')
        if kind in ('input', 'inout', 'output'):
            if use[0] != 'data':
                raise ValueError(f'{param} is a buffer but its argument is not t.data_ptr<T>()')
            want(tensors, use[1])
            tensor_of[resource['name']] = use[1]
            if kind in ('input', 'inout'):
                inputs[resource['name']] = use[1]
            if kind in ('inout', 'output'):
                stores.append((use[1], resource['name']))
        elif kind == 'shape':
            if use[0] == 'size':
                want(tensors, use[1])
                shapes[resource['name']] = f'({_identifier(use[1])}.size(0), 1, 1)'
            elif use[0] == 'parameter':
                want(scalars, use[1])
                shapes[resource['name']] = f'_length({_identifier(use[1])}, {use[1]!r})'
            elif use[0] == 'literal':
                shapes[resource['name']] = f'_length({use[1]}, {param!r})'
            else:
                raise ValueError(f'{param} is a length but its argument is a tensor')
        elif kind == 'value':
            if use[0] == 'parameter':
                want(scalars, use[1])
                values[resource['name']] = _identifier(use[1])
            elif use[0] == 'literal':
                values[resource['name']] = use[1]
            elif use[0] == 'size':
                want(tensors, use[1])
                values[resource['name']] = f'{_identifier(use[1])}.size(0)'
            else:
                raise ValueError(f'{param} is a scalar but its argument is a tensor')
        else:  # scratch: memory the caller would see, which the package keeps to itself
            raise ValueError(f'{param} is {kind} memory in the package, but the original passes a tensor for it')
    for r in plan['resources']:  # shapes no parameter carries: from a buffer they size
        if r['kind'] == 'shape' and r['name'] not in shapes:
            for b in plan['resources']:
                if b.get('extent') == r['name'] and b['name'] in tensor_of and not b.get('length'):
                    t = _identifier(tensor_of[b['name']])
                    shapes[r['name']] = f'({t}.numel() * {t}.element_size() // {_bytes(b)}, 1, 1)'
                    break
            else:
                raise ValueError(f'nothing at the launch gives the shape {r["name"]}')
    from generate_metal_host import BUFFER_TYPES
    from qpel_types import element
    name = _identifier(launch.function)
    # the host function's parameters in its order, then tensors it makes itself (outputs), as they appear
    tensors = [t for t in launch.parameters if t in tensors] + [t for t in tensors if t not in launch.parameters]
    scalars = [t for t in launch.parameters if t in scalars]
    args = [_identifier(t) for t in tensors] + [_identifier(s) for s in scalars]
    lines = [f'def {name}({", ".join(args)}):',
             f'    """The launch of {launch.written} in {Path(launch.where[0]).name}:{launch.where[1]} '
             f'({launch.function}), on the Apple GPU."""']
    for t in tensors:
        lines.append(f'    _tensor({_identifier(t)}, {t!r})')
    inputs_text = ', '.join(f'{r!r}: _flat({_identifier(t)}, {t!r}, torch.{BUFFER_TYPES[element(resources[r]["type"])]})'
                            for r, t in inputs.items())
    lines.append(f'    shapes = {{{", ".join(f"{k!r}: {v}" for k, v in shapes.items())}}}')
    lines.append(f'    out = _host().run({{{inputs_text}}}, shapes, {{{", ".join(f"{k!r}: {v}" for k, v in values.items())}}})')
    for t, r in stores:
        lines.append(f'    _store({_identifier(t)}, {t!r}, out[{r!r}])')
    return name, '\n'.join(lines) + '\n'


def _bytes(resource):
    """Bytes of one extent element of a buffer: element size times its scale."""
    from qpel_types import element
    sizes = {'u8': 1, 'i8': 1, 'u16': 2, 'i16': 2, 'f16': 2, 'bf16': 2, 'u32': 4, 'i32': 4, 'f32': 4, 'u64': 8, 'i64': 8}
    return sizes[element(resource['type'])] * resource.get('scale', 1)


def generate(package, host_sources, include=()):
    """(torch_ops.py text, {launch: reason it got no function}) for a built package."""
    generated = Path(package)/'generated'
    if not (generated/'pipeline.json').is_file():
        raise ValueError(f'{package} is not a built package (no generated/pipeline.json)')
    plan = json.loads((generated/'pipeline.json').read_text())
    program = json.loads((Path(package)/'program.qpel.json').read_text())
    if len(plan['stages']) != 1:
        raise ValueError('torch-op supports packages with one dispatch')
    kernel = plan['stages'][0]['kernel']
    parameters = kernel_parameters(program, kernel)
    functions, skipped, names = [], {}, {}
    for source in host_sources:
        for launch in launches(source, include):
            if not _matches(kernel, launch.written):
                continue
            label = f'{Path(launch.where[0]).name}:{launch.where[1]} ({launch.function})'
            for arguments in reversed(launch.variants):  # instantiations first
                launch.arguments = arguments
                try:
                    name, text = _generate_one(launch, plan, kernel, parameters)
                    break
                except ValueError as error:
                    skipped[label] = str(error)
            else:
                continue
            skipped.pop(label, None)
            names[name] = names.get(name, 0) + 1
            if names[name] > 1:  # several launches in one host function
                text = text.replace(f'def {name}(', f'def {name}_{names[name]}(', 1)
            functions.append(text)
    if not functions:
        raise ValueError(f'no launch of {kernel} could be wrapped: ' +
                         ('; '.join(f'{k}: {v}' for k, v in skipped.items()) or 'none found'))
    return PRELUDE + '\n\n'.join(functions), skipped


def write(package, host_sources, include=()):
    text, skipped = generate(package, host_sources, include)
    path = Path(package)/'generated'/'torch_ops.py'
    path.write_text(text)
    return path, skipped
