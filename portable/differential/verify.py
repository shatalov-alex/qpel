"""qpel verify: does a port behave like its original?

    qpel verify original.cu port.metal [--kernel NAME ...] [--map ORIGINAL=PORT ...]
                [--arg PORT_ARG=EXPR ...] [--value NAME=RULE ...] [--constant NAME=VALUE ...]
                [-I DIR] [--nvidia] [--metal gpu|cpu|preflight] [--json FILE]

The original is the reference. It is imported to qpel, which gives the launch plan (its
kernels, resources and how each argument is fed), the cases and the numeric tolerance. The port
is only read for its kernels' signatures: it need not import to qpel, so a hand-written or
AI-generated port with constructs qpel does not cover can still be checked.

Each port kernel (same name, or --map) gets the original's data through its own arguments,
builtins such as [[thread_position_in_grid]] aside. An argument takes, in this order: the
expression --arg gives it; the original's argument of the same name (or a field of the
original's argument struct of that name); otherwise the original's next unclaimed argument, by
position. A struct argument (a Metal `constant Args& args`) is filled field by field the same
way (--arg args.scale=scale). --arg expressions are numbers and the original's scalar arguments
(struct fields as args.field), W, H and N of the dispatch shape, + - * / and parentheses.
Buffers must have the original's element type, or be byte buffers (char*). Integer scalars of
another width are converted; floats and integers are not mixed. Struct fields left unmatched
are zero, and are listed.

The port is launched over the original's dispatch: the same grid and threadgroup size. A Metal
port gets an exact thread count, so its last threadgroup may be partial (dispatchThreads);
with --dispatch groups it gets whole threadgroups, as CUDA launches blocks (dispatchThreadgroups,
which ggml's Metal host uses: its kernels index with threads_per_threadgroup).

Both sides then run the same cases (the behaviour matrix's, plus copies whose input buffers
mix in edge values: NaN, infinities, signed zeros, subnormals, type limits), each where it runs
here: CUDA on the CPU simulator (the CUDA semantics qpel's behaviour rows use), or with --nvidia
on GPGPU-Sim, a simulated NVIDIA GPU in Docker; Metal on the Apple GPU, or with --metal cpu in
the same CPU simulator (differential/metal_cpu.py), where a case that runs longer than --timeout
seconds is stopped. A Metal kernel that never ends (a probe or retry loop given a wrong size)
cannot be stopped on an Apple GPU, and repeated hangs can restart the machine: run a port whose
loops are not known to end with --metal preflight, which uses the GPU only after every case
finished on the CPU. When the original is a
pinned corpus program (corpus/corpus.json), its value rules, function constants, geometry and
case sizes are used. Outputs are compared under NUMERICS.md as in the behaviour matrix:
bit-exact, or within twice the stated ulp bound where the original calls bounded math
functions; +0 and -0 count as equal when either side is Metal (Apple's GPUs flush zero signs).
The first difference of each failing case is reported.

Exit status: 0 when every case agrees, 1 when one differs, 2 when the port cannot be run.
"""
import argparse
import ast
import copy
import json
import operator
from pathlib import Path
import re
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from frontends import clang_ast  # noqa: E402
from frontends.c_import import ATOMIC_SCALARS, Importer, describe, import_source, params_of  # noqa: E402
from qpel_ir import Lowering  # noqa: E402
from qpel_types import EXACT_MATH, MATH_ULPS, INTEGER_TERMS, bits as type_bits, is_struct  # noqa: E402

from . import cases as test_cases  # noqa: E402
from .simulate import Case, volume  # noqa: E402


class PortError(ValueError):
    """The port cannot be run against the original (a missing kernel, a signature that does not map)."""


BYTES = ('i8', 'u8')
FLOATS = ('f16', 'bf16', 'f32', 'f64')


def element(qual_type):
    """The element or scalar type of a parameter, for comparing the two signatures (a Metal
    atomic_uint buffer holds u32)."""
    kind = describe(qual_type)
    if kind[0] == 'pointer':
        return ATOMIC_SCALARS.get(kind[2], kind[2])
    return kind[1] if kind[0] == 'scalar' else kind[-1]


def evaluate(expression, names):
    """A number from an --arg expression: numbers, names, + - * / (C's truncating division
    between integers) and parentheses."""
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}

    def walk(n):
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, (ast.Name, ast.Attribute)):
            name = ast.unparse(n)
            if name not in names:
                raise PortError(f'--arg: {name} is not an argument of the original (it has {", ".join(sorted(names))})')
            return names[name]()
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = walk(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and type(n.op) in binary:
            return binary[type(n.op)](walk(n.left), walk(n.right))
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Div, ast.FloorDiv)):
            a, b = walk(n.left), walk(n.right)
            if isinstance(a, int) and isinstance(b, int):
                return abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
            return a / b
        raise PortError(f'--arg: {ast.unparse(n)} is not a number, a name or + - * /')
    try:
        tree = ast.parse(expression, mode='eval')
    except SyntaxError:
        raise PortError(f'--arg: cannot read {expression!r}') from None
    return walk(tree)


def convert(value, t):
    """value as a scalar of type t: integers wrap to its width, floats stay floats."""
    if t in FLOATS:
        return float(value)
    if t == 'bool':
        return int(bool(value))
    if isinstance(value, float):
        value = int(value)
    width = type_bits(t)
    value %= 2**width
    return value - 2**width if t.startswith('i') and value >= 2**(width - 1) else value


class Offer:
    """One thing the original passes a kernel: a buffer, a scalar, a struct or a shape."""

    def __init__(self, name, kind, t, record=None, getter=None, fields=None, parent=None):
        self.name, self.kind, self.type, self.record, self.getter = name, kind, t, record, getter
        self.fields, self.parent = fields or {}, parent


def offers(kernel, original, plan):
    """{name: Offer} for every argument the original passes `kernel`, and its struct fields."""
    resources = {r['name']: r for r in plan['resources']}
    stage = next(s for s in plan['stages'] if s['kernel'] == kernel)
    params = {b['parameter']: b['resource'] for b in stage['bindings']}
    out = {}
    for a in original['arguments'][kernel]:
        source, parameter = a['source'], a.get('parameter')
        if source == 'extent':
            out[a['name']] = Offer(a['name'], 'scalar', a['type'], a,
                                   lambda c, b, a=a: c.shapes[b[a['parameter']]]['xyz'.index(a['axis'])])
        elif source == 'volume':
            out[a['name']] = Offer(a['name'], 'scalar', a['type'], a, lambda c, b, a=a: volume(c.shapes[b[a['parameter']]]))
        elif source == 'parameter':
            r = resources[params[parameter]]
            if r['kind'] == 'shape':
                out[a['name']] = Offer(a['name'], 'shape', 'shape3', a)
            elif r['kind'] == 'value' and is_struct(r['type']):
                fields = dict(map(tuple, plan['structs'][r['type'][7:]]))
                out[a['name']] = Offer(a['name'], 'struct', r['type'], a, fields=fields)
                for f, t in fields.items():
                    getter = lambda c, b, a=a, f=f: c.values[b[a['parameter']]][f]
                    out[f'{a["name"]}.{f}'] = Offer(f'{a["name"]}.{f}', 'scalar', t, getter=getter, parent=a['name'])
                    if f.rstrip('_') != f:  # args.min_: the importer's name for a field called min
                        out.setdefault(f'{a["name"]}.{f.rstrip("_")}', out[f'{a["name"]}.{f}'])
            elif r['kind'] == 'value':
                out[a['name']] = Offer(a['name'], 'scalar', a['type'], a, lambda c, b, a=a: c.values[b[a['parameter']]])
            else:
                out[a['name']] = Offer(a['name'], 'buffer', a['type'], a)
        else:
            out[a['name']] = Offer(a['name'], 'unknown', a['type'], a)
    return out


class Port:
    """The port's kernels mapped onto the original's: report (as an import report would be) and,
    for arguments the original does not pass as they are, the values to compute per case."""

    def __init__(self, path, original, plan, include=(), mapping=None, arguments=None):
        path = Path(path)
        self.path, self.plan = path, plan
        dialect = clang_ast.dialect_of(path)
        unit, source = clang_ast.parse(path, dialect, include)
        importer = Importer(unit, source, path, dialect, original['module'])
        by_name = {d['name']: d for d in importer.decls}
        explicit = dict(arguments or {})
        self.synthetic = {}  # kernel -> [(parameter, type, compute(case, bindings, shape))]
        self.structs = {}
        self.mapped, self.unused, self.zeroed = {}, {}, {}
        report_arguments, qualified = {}, {}
        for kernel in dict.fromkeys(s['kernel'] for s in plan['stages']):
            name = (mapping or {}).get(kernel, kernel)
            decl = by_name.get(name)
            if decl is None or not importer.dialect.is_kernel(decl):
                kernels = sorted(d['name'] for d in importer.decls if importer.dialect.is_kernel(d))
                shown = ', '.join(kernels[:20]) + (f', ... ({len(kernels)} in all)' if len(kernels) > 20 else '')
                raise PortError(f'{path.name} has no kernel {name} (kernels: {shown or "none"}); '
                                f'use --map {kernel}=PORT_KERNEL')
            params = [p for p in params_of(decl) if not importer.dialect.builtin(p)]
            theirs = offers(kernel, original, plan)
            top = [o for o in theirs.values() if o.record is not None]
            records, explained, used = [], [], set()
            claims, done = {}, {}

            def kind_of(p):
                kind = describe(p['type']['qualType'])
                if kind[0] == 'pointer':
                    return 'buffer'
                if kind[0] == 'scalar' and kind[1] in importer.records:
                    return 'struct'
                return 'shape' if kind[0] == 'shape' else 'scalar'
            for p in params:  # explicit and same-name matches first
                if p['name'] in explicit:
                    claims[p['name']] = ('expression', explicit[p['name']])
                    if p['name'] in theirs:  # the same argument, given another way
                        used.add(theirs[p['name']].parent or p['name'])
                elif p['name'] in theirs:
                    o = theirs[p['name']]
                    claims[p['name']] = ('offer', o)
                    used.add(o.parent or o.name)
                elif kind_of(p) != 'struct':
                    field = next((o for o in theirs.values() if o.parent and o.name.split('.', 1)[1] == p['name']), None)
                    if field:
                        claims[p['name']] = ('offer', field)
                        used.add(field.parent)
            names = {o.name: (lambda o=o: o) for o in theirs.values() if o.kind == 'scalar'}
            for position, p in enumerate(params):  # structs take their fields by name
                if kind_of(p) == 'struct':
                    how, what = claims.get(p['name'], ('offer', None))
                    done[p['name']] = self.argument(kernel, importer, p, position, how, what, theirs, explicit, names, used)
            for kind in ('buffer', 'scalar', 'shape'):  # the rest by position, buffers with buffers
                pending = [p for p in params if p['name'] not in claims and p['name'] not in done and kind_of(p) == kind]
                left = [o for o in top if o.name not in used and o.kind == kind]
                if pending and len(pending) != len(left):
                    raise PortError(f'{name} takes {len(params)} arguments ({", ".join(p["name"] for p in params)}); '
                                    f'the original {kernel} takes {len(top)} ({", ".join(o.name for o in top)}), and '
                                    f'{", ".join(p["name"] for p in pending)} match none by name: '
                                    f'use --arg PORT_ARG=EXPR')
                for p, o in zip(pending, left):
                    claims[p['name']] = ('offer', o)
                    used.add(o.name)
            for position, p in enumerate(params):
                if p['name'] in done:
                    record, note = done[p['name']]
                else:
                    how, what = claims[p['name']]
                    record, note = self.argument(kernel, importer, p, position, how, what, theirs, explicit, names, used)
                records.append(record)
                explained.append({'port': p['name'], 'from': note})
            referenced = set()
            for text in explicit.values():
                for n in re.findall(r'[A-Za-z_]\w*(?:\.\w+)?', text):
                    referenced.update({n, n.split('.')[0]})
            for o in top:
                if o.name in used or o.name in referenced or o.kind == 'shape':
                    continue
                if o.kind == 'buffer' and self.is_output(kernel, o):
                    raise PortError(f'{name} receives no argument for the original\'s output {o.name}')
                self.unused.setdefault(kernel, []).append(o.name)
            report_arguments[kernel] = records
            qualified[kernel] = importer.qualified.get(name, name)
            self.mapped[kernel] = explained
        self.report = {'source': str(path), 'dialect': dialect, 'module': original['module'],
                       'kernels': list(report_arguments), 'qualified': qualified, 'arguments': report_arguments,
                       'host_spans': importer.host_spans, 'header_host_bodies': importer.header_host_bodies}

    def is_output(self, kernel, offer):
        stage = next(s for s in self.plan['stages'] if s['kernel'] == kernel)
        resource = next(b['resource'] for b in stage['bindings'] if b['parameter'] == offer.record['parameter'])
        return next(r for r in self.plan['resources'] if r['name'] == resource)['kind'] in ('output', 'inout')

    def argument(self, kernel, importer, p, position, how, what, theirs, explicit, names, used):
        """(the import-report record for port parameter p, how it is fed)."""
        kind = describe(p['type']['qualType'])
        binding = importer.dialect.binding(p, position)
        ours = element(p['type']['qualType'])
        base = {'name': p['name'], 'binding': binding, 'type': ours}
        if kind[0] == 'scalar' and kind[1] in importer.records:  # an argument struct
            struct = importer.struct_type(kind[1], p)[7:]
            fields = importer.structs[struct]
            self.structs[struct] = [list(f) for f in fields]
            raw = [f.get('name') for f in importer.records[kind[1]].get('inner', ()) if f.get('kind') == 'FieldDecl']
            source = what if how == 'offer' and what is not None and what.kind == 'struct' else None
            if source is not None:
                used.add(source.name)
            computed, notes, zeroed = {}, [], []
            for (field, t), written in zip(fields, raw):
                key = next((k for k in (f'{p["name"]}.{written}', f'{p["name"]}.{field}') if k in explicit), None)
                if key:
                    computed[field] = self.expression(explicit[key], t, names)
                    notes.append(f'{written}={explicit[key]}')
                    continue
                offer = None
                if source is not None:
                    offer = theirs.get(f'{source.name}.{field}') or theirs.get(f'{source.name}.{written}')
                offer = offer or (theirs.get(written) if theirs.get(written) and theirs[written].kind == 'scalar' else None)
                if offer is None:
                    zeroed.append(written)
                    computed[field] = lambda c, b, s: 0
                    continue
                self.check_scalar(kernel, p, position, offer, t)
                used.add(offer.parent or offer.name)
                computed[field] = lambda c, b, s, offer=offer, t=t: convert(offer.getter(c, b), t)
                notes.append(f'{written}={offer.name}' if offer.name != written else written)
            if zeroed:
                self.zeroed.setdefault(kernel, []).extend(f'{p["name"]}.{f}' for f in zeroed)
            self.synthetic.setdefault(kernel, []).append(
                (p['name'], 'struct ' + struct, lambda c, b, s, computed=computed: {f: g(c, b, s) for f, g in computed.items()}))
            return dict(base, type='struct ' + struct, source='parameter', parameter='qpel_port_' + p['name']), \
                f'struct {struct}: ' + (', '.join(notes) or 'nothing') + (f'; zero: {", ".join(zeroed)}' if zeroed else '')
        if how == 'expression':
            if kind[0] != 'scalar':
                raise PortError(f'--arg {p["name"]}: only scalar arguments and struct fields take expressions')
            self.synthetic.setdefault(kernel, []).append((p['name'], ours, self.expression(what, ours, names)))
            return dict(base, source='parameter', parameter='qpel_port_' + p['name']), what
        offer = what
        if offer.kind == 'buffer':
            if kind[0] != 'pointer':
                raise PortError(f'{p["name"]} (argument {position}) is {ours}; the original passes the buffer {offer.name}')
            if ours != offer.type and ours not in BYTES and offer.type not in BYTES:
                raise PortError(f'{kernel} argument {position} ({p["name"]}) is {ours}; the original\'s '
                                f'({offer.name}) is {offer.type}')
            return dict(offer.record, name=p['name'], binding=binding), offer.name + (' (as bytes)' if ours != offer.type else '')
        if offer.kind == 'shape':
            if kind[0] != 'shape':
                raise PortError(f'{p["name"]} (argument {position}) is {ours}; the original passes the shape {offer.name}')
            return dict(offer.record, name=p['name'], binding=binding), offer.name
        if offer.kind == 'struct':
            raise PortError(f'{p["name"]} (argument {position}) is {ours}; the original passes the struct {offer.name}: '
                            f'name its field with --arg {p["name"]}={offer.name}.FIELD')
        if offer.kind != 'scalar' or kind[0] not in ('scalar',):
            raise PortError(f'{kernel} argument {position} ({p["name"]}) is {ours}; the original\'s ({offer.name}) is {offer.type}')
        self.check_scalar(kernel, p, position, offer, ours)
        if offer.record is not None and offer.type == ours:
            return dict(offer.record, name=p['name'], binding=binding), offer.name
        self.synthetic.setdefault(kernel, []).append(
            (p['name'], ours, lambda c, b, s, offer=offer: convert(offer.getter(c, b), ours)))
        return dict(base, source='parameter', parameter='qpel_port_' + p['name']), \
            offer.name + (f' (converted from {offer.type})' if offer.type != ours else '')

    @staticmethod
    def check_scalar(kernel, p, position, offer, ours):
        if (ours in FLOATS) != (offer.type in FLOATS) or not (ours in FLOATS or ours in INTEGER_TERMS or ours == 'bool'):
            raise PortError(f'{kernel} argument {position} ({p["name"]}) is {ours}; the original\'s '
                            f'({offer.name}) is {offer.type}')

    @staticmethod
    def expression(text, t, names):
        def compute(c, b, s):
            scope = {n: (lambda o=get(): o.getter(c, b)) for n, get in names.items()}
            scope.update({k: (lambda v=v: v) for k, v in zip('WHN', s)})
            return convert(evaluate(text, scope), t)
        return compute

    def dropped(self):
        """The original's struct values the port does not take as they are (its own C code has no
        such type, so the simulator must not declare them)."""
        taken = {a.get('parameter') for records in self.report['arguments'].values() for a in records}
        return {b['resource'] for s in self.plan['stages'] for b in s['bindings'] if b['parameter'] not in taken} & \
            {r['name'] for r in self.plan['resources'] if r['kind'] == 'value' and is_struct(r['type'])}

    def adapt_plan(self):
        """The original's plan, plus a value resource for every port argument computed per case."""
        plan = copy.deepcopy(self.plan)
        dropped = self.dropped()
        plan['resources'] = [r for r in plan['resources'] if r['name'] not in dropped]
        if self.structs:
            plan.setdefault('structs', {}).update(self.structs)
        for i, s in enumerate(plan['stages']):
            for parameter, t, _ in self.synthetic.get(s['kernel'], ()):
                resource = f'qpel_port_{i}_{parameter}'
                plan['resources'].append({'name': resource, 'kind': 'value', 'type': t, 'extent': None})
                s['bindings'].append({'parameter': 'qpel_port_' + parameter, 'resource': resource})
        return plan

    def adapt_case(self, case):
        dropped = self.dropped()
        values = {k: v for k, v in case.values.items() if k not in dropped}
        for i, s in enumerate(self.plan['stages']):
            bindings = {b['parameter']: b['resource'] for b in s['bindings']}
            for parameter, _, compute in self.synthetic.get(s['kernel'], ()):
                values[f'qpel_port_{i}_{parameter}'] = compute(case, bindings, case.shapes[s['grid']])
        return Case(case.shapes, case.inputs, values, case.label, case.defined)


def port_report(path, original, plan, include=(), mapping=None, arguments=None):
    """An import report for the port, made from its signatures alone (see Port)."""
    port = Port(path, original, plan, include, mapping, arguments)
    return dict(port.report, mapping=port.mapped, unused=port.unused, zeroed=port.zeroed)


def declared_constants(path, include=()):
    """Names of the Metal function constants a source declares."""
    if Path(path).suffix != '.metal':
        return set()
    from .metal_text import inline_includes
    return set(re.findall(r'constant\s+\w+\s+(\w+)\s*\[\[\s*function_constant', inline_includes(path, include)))


def corpus_program(path, kernels):
    """The pinned corpus program (options as the behaviour matrix uses them) for this original
    and these kernels, or None."""
    try:
        from frontends import roundtrip  # the repository's corpus; not in the pip package
    except ImportError:
        return None
    path = Path(path).resolve()
    for entry, program in roundtrip.corpus_programs():
        if (roundtrip.CORPUS/entry['source']).resolve() == path and (kernels is None or set(kernels) == set(program['kernels'])):
            include = [roundtrip.CORPUS/f for f in entry.get('include', ())]
            return program, include
    return None


class Side:
    """One source and the way it runs here."""

    def __init__(self, path, plan, report, include, work, constants=None, nvidia=False, whole_groups=False,
                 metal='gpu', timeout=60):
        self.path, self.plan, self.report, self.include, self.constants = Path(path), plan, report, include, constants
        self.whole_groups = whole_groups
        self.dialect, self.nvidia, self.work = report['dialect'], nvidia and report['dialect'] == 'cuda', Path(work)
        self.on_cpu = self.dialect == 'metal' and metal == 'cpu'
        if self.on_cpu:  # a Metal source in the CUDA simulator: a loop that never ends times out
            from . import metal_cpu
            try:
                self.program = metal_cpu.program(self.path, plan, report, work, include, constants, whole_groups, timeout)
            except (RuntimeError, ValueError) as error:
                raise PortError(f'{self.path.name} cannot run on the CPU: {error}') from None
        elif self.dialect == 'cuda':
            Path(work).mkdir(parents=True)
            if self.nvidia:
                try:
                    from . import gpgpusim  # in the repository only: it needs gpgpusim/ and the corpus
                except ImportError:
                    raise PortError('--nvidia runs from the qpel repository (portable/gpgpusim)') from None
                self.program = gpgpusim.GpuProgram(self.path, plan, work/'gpu', fmad=False, original=report, include=include)
            else:
                from .simulate import Program
                self.program = Program(self.path, plan, work, original=report, include=include)
        else:
            from . import metal
            if not metal.available():
                raise PortError('running Metal needs PyTorch with an Apple GPU (MPS)')

    @property
    def where(self):
        if self.dialect == 'cuda':
            return 'GPGPU-Sim (CUDA, simulated NVIDIA GPU)' if self.nvidia else 'CPU simulator (CUDA)'
        return 'CPU simulator (Metal)' if self.on_cpu else 'Apple GPU (Metal)'

    def run(self, case):
        if self.dialect == 'cuda' or self.on_cpu:
            return self.program.run(case)
        from . import metal
        return metal.run_original(self.path, self.plan, self.report, case, self.include, self.constants, self.whole_groups)

    def run_all(self, found):
        """[(outputs, None) or (None, error)] for every case: GPGPU-Sim runs them all in one container."""
        if not self.nvidia:
            out = []
            for case in found:
                try:
                    out.append((self.run(case), None))
                except RuntimeError as error:
                    out.append((None, str(error)))
            return out
        from . import gpgpusim
        if not gpgpusim.image_ready(build=False):
            raise PortError(f'--nvidia needs Docker and the GPGPU-Sim image {gpgpusim.IMAGE} (gpgpusim/README.md)')
        folders = []
        for number, case in enumerate(found):
            folder = self.program.work/f'c{number:03}'
            folder.mkdir()
            self.program.write_case(case, folder)
            folders.append(folder)
        mounts = [self.work.resolve(), HERE, self.path.resolve().parent] + [Path(f).resolve() for f in self.include]
        gpgpusim.run_all(self.work.resolve(), mounts)  # programs one level down, cases two
        return [self.program.outcome(f) for f in folders]


def verify(original, port, kernels=None, mapping=None, include=(), constants=None, edges=True, arguments=None,
           values=None, nvidia=False, dispatch='threads', metal='gpu', timeout=60):
    """{'cases': [...], 'passed': bool, ...}: the port against the original on every case.
    dispatch: how a Metal port is launched, 'threads' (an exact thread count) or 'groups' (whole
    threadgroups). metal: where Metal runs, 'gpu', 'cpu' (the CUDA simulator, each case stopped
    after `timeout` seconds), or 'preflight' (the CPU first; the GPU only if every case finished
    there, since a Metal kernel that never ends cannot be stopped on an Apple GPU)."""
    if dispatch not in ('threads', 'groups'):
        raise ValueError(f'dispatch is threads or groups, not {dispatch!r}')
    if metal not in ('gpu', 'cpu', 'preflight'):
        raise ValueError(f'metal is gpu, cpu or preflight, not {metal!r}')
    from .compare import compare
    original, port = Path(original), Path(port)
    options, fixture_plan, module = {}, None, None
    pinned = corpus_program(original, kernels)
    if pinned:  # imported and launched exactly as the behaviour matrix does it
        from frontends import roundtrip
        program_options, corpus_include = pinned
        options = {k: program_options.get(k) for k in ('values', 'constants', 'geometry', 'shapes', 'inputs', 'outputs')}
        kernels, module = program_options['kernels'], program_options['fixture']
        fixture_plan = Lowering(roundtrip.fixture(module)).plan()
        include = list(include) + [f for f in corpus_include if f not in include]
    constants = dict(options.get('constants') or {}, **(constants or {}))
    rules = dict(options.get('values') or {}, **(values or {}))
    ours = {k: v for k, v in constants.items() if k in declared_constants(original, include)}
    theirs_constants = {k: v for k, v in constants.items() if k in declared_constants(port, include)}
    program, report = import_source(original, include=include, kernels=kernels, constants=ours, plan=fixture_plan,
                                    module=module, geometry=None if fixture_plan else options.get('geometry'))
    plan = fixture_plan or Lowering(program).plan()
    mapped = Port(port, report, plan, include, mapping, arguments)
    theirs = mapped.report
    outputs = [r['name'] for r in plan['resources'] if r['kind'] in ('output', 'inout')]
    bounded = any(n['kind'] == 'Math' and n['attributes']['function'] not in EXACT_MATH for n in program['nodes'])
    ulps = 2 * MATH_ULPS if bounded else 0
    zero_sign = report['dialect'] == 'cuda' and theirs['dialect'] == 'cuda'
    result = {'original': str(original), 'port': str(port), 'module': report['module'],
              'kernels': {k: theirs['qualified'][k] for k in theirs['kernels']}, 'arguments': mapped.mapped,
              'unused': mapped.unused, 'zeroed': mapped.zeroed, 'ulps': ulps, 'corpus': bool(pinned), 'cases': []}
    work = Path(tempfile.mkdtemp(prefix='qpel-verify-')).resolve()
    try:
        def sides(where, folder):
            return (Side(original, plan, report, include, work/folder/'original', ours, nvidia, metal=where,
                         timeout=timeout),
                    Side(port, mapped.adapt_plan(), theirs, include, work/folder/'port', theirs_constants, nvidia,
                         whole_groups=dispatch == 'groups', metal=where, timeout=timeout))

        def run(reference, candidate):
            expected = reference.run_all(found)
            runnable = [i for i, (_, failed) in enumerate(expected) if not failed]  # nothing to compare otherwise
            ran = dict(zip(runnable, candidate.run_all([mapped.adapt_case(found[i]) for i in runnable])))
            return expected, [ran.get(i, (None, None)) for i in range(len(found))]
        reference, candidate = sides('cpu' if metal != 'gpu' else 'gpu', 'first')
        result['runs'] = {'original': reference.where, 'port': candidate.where}
        found = test_cases.for_program(report['module'], plan, values=rules, shapes=options.get('shapes'),
                                       inputs=options.get('inputs'))
        # No subnormal inputs where Metal runs: Apple GPUs flush them (NUMERICS.md), a platform difference.
        edged = edges and not test_cases.structured(report['module'])
        result['edges'] = 'yes' if edged else 'no' if not edges else 'not for these structured inputs'
        found += test_cases.with_edges(found, plan, 20261111, subnormals=zero_sign) if edged else []
        expected, actual = run(reference, candidate)
        uses_metal = 'metal' in (report['dialect'], theirs['dialect'])
        if metal == 'preflight' and uses_metal:
            stuck = [c.label for c, (_, a), (_, b) in zip(found, expected, actual) if 'did not finish' in (a or b or '')]
            if stuck:
                result['preflight'] = (f'on the CPU, {len(stuck)} case(s) did not finish within {timeout} s '
                                       f'({stuck[0]}, ...); the Apple GPU was not used')
            else:
                result['preflight'] = 'every case finished on the CPU; the results are the Apple GPU\'s'
                reference, candidate = sides('gpu', 'gpu')
                result['runs'] = {'original': reference.where, 'port': candidate.where}
                expected, actual = run(reference, candidate)
        for case, (want, failed), (got, broke) in zip(found, expected, actual):
            entry = {'case': case.label}
            if failed and case.label.endswith('edge values'):  # such inputs are outside what the original accepts
                entry['skipped'] = f'the original fails on these inputs: {failed[:300]}'
            elif failed:
                entry['error'] = f'the original failed: {failed}'
            elif broke:
                entry['error'] = f'the port failed: {broke}'
            else:
                difference = compare(want, got, outputs, case.defined, ulps, options.get('outputs'), zero_sign)
                if difference:
                    entry['difference'] = difference.replace(', qpel ', ', port ')
            result['cases'].append(entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result['passed'] = not any('error' in c or 'difference' in c for c in result['cases'])
    return result


def pairs(items, option, parser):
    out = {}
    for item in items:
        if '=' not in item:
            parser.error(f'{option} {item}: expected NAME=VALUE')
        a, b = item.split('=', 1)
        out[a.strip()] = b.strip()
    return out


def literal(text):
    """A --value or --constant value: true/false, an integer, a float, or (for --value) 'volume'
    or an expression of W, H and N."""
    if text in ('true', 'false'):
        return text == 'true'
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(prog='qpel verify', description=__doc__.split('\n\n')[0])
    parser.add_argument('original', type=Path, help='the reference: a CUDA (.cu) or Metal (.metal) source')
    parser.add_argument('port', type=Path, help='the port to check against it')
    parser.add_argument('--kernel', action='append', dest='kernels', metavar='NAME',
                        help='verify only this kernel of the original (repeatable)')
    parser.add_argument('--map', action='append', default=[], metavar='ORIGINAL=PORT',
                        help='the port names a kernel differently (repeatable)')
    parser.add_argument('--arg', action='append', default=[], metavar='PORT_ARG=EXPR',
                        help="feed a port argument or struct field (args.ne0) from the original's arguments, "
                             'W, H, N and numbers (repeatable)')
    parser.add_argument('--value', action='append', default=[], metavar='NAME=RULE',
                        help="constrain an original scalar in the cases: 'volume' (the element count), "
                             'a number, or an expression of W, H and N (repeatable)')
    parser.add_argument('--constant', action='append', default=[], metavar='NAME=VALUE',
                        help='a Metal function constant, for whichever side declares it (repeatable)')
    parser.add_argument('--dispatch', choices=('threads', 'groups'), default='threads',
                        help='launch a Metal port with an exact thread count (dispatchThreads, the default) or with '
                             'whole threadgroups (dispatchThreadgroups, as CUDA launches blocks and as ggml does)')
    parser.add_argument('-I', action='append', default=[], dest='include', type=Path, metavar='DIR',
                        help='include directory for both sources (repeatable)')
    parser.add_argument('--nvidia', action='store_true',
                        help='run CUDA on GPGPU-Sim (a simulated NVIDIA GPU, in Docker) instead of the CPU simulator')
    parser.add_argument('--metal', choices=('gpu', 'cpu', 'preflight'), default='gpu',
                        help='run Metal on the Apple GPU (default), on the CPU simulator, or on the CPU first and '
                             'on the GPU only if every case finished there (for kernels whose loops may not end)')
    parser.add_argument('--timeout', type=float, default=60, metavar='SECONDS',
                        help='stop a case on the CPU simulator after this long (default 60)')
    parser.add_argument('--json', type=Path, metavar='FILE', help='write the machine-readable report here')
    parser.add_argument('--no-edges', action='store_true',
                        help='leave out the cases with edge values (NaN, infinities, signed zeros, subnormals, limits)')
    args = parser.parse_args(argv)
    mapping = pairs(args.map, '--map', parser)
    arguments = pairs(args.arg, '--arg', parser)
    values = {k: literal(v) for k, v in pairs(args.value, '--value', parser).items()}
    constants = {k: literal(v) for k, v in pairs(args.constant, '--constant', parser).items()}
    try:
        result = verify(args.original, args.port, args.kernels, mapping, args.include, constants,
                        edges=not args.no_edges, arguments=arguments, values=values, nvidia=args.nvidia,
                        dispatch=args.dispatch, metal=args.metal, timeout=args.timeout)
    except (PortError, ValueError, RuntimeError) as error:
        result = {'original': str(args.original), 'port': str(args.port), 'passed': False, 'error': str(error)}
        print(f'qpel verify: {error}', file=sys.stderr)
        if args.json:
            args.json.write_text(json.dumps(result, indent=1) + '\n')
        return 2
    print(summary(result, args.original.name, args.port.name))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + '\n')
    return 0 if result['passed'] else 1


def summary(result, original, port):
    """The human-readable report: one line per case, the argument mapping when it is not plain,
    and the totals."""
    lines = []
    for c in result['cases']:
        problem = c.get('error') or c.get('difference')
        status = 'FAIL' if problem else 'SKIP' if 'skipped' in c else 'PASS'
        lines.append(f'{status} {c["case"]}' + (f'\n     {problem or c["skipped"]}' if problem or 'skipped' in c else ''))
    for kernel, mapped in result.get('arguments', {}).items():
        if any(m['port'] != m['from'] for m in mapped):
            lines.append(f'arguments of {result["kernels"][kernel]}: ' +
                         '; '.join(m['port'] if m['port'] == m['from'] else f'{m["port"]} <- {m["from"]}' for m in mapped))
    for kernel, names in result.get('unused', {}).items():
        lines.append(f'note: the port\'s {result["kernels"][kernel]} is given nothing from {", ".join(names)}')
    failed = sum(1 for c in result['cases'] if 'error' in c or 'difference' in c)
    skipped = sum(1 for c in result['cases'] if 'skipped' in c)
    kernels = ', '.join(f'{k} -> {v}' if k != v else k for k, v in result['kernels'].items())
    lines.append(f'{port} against {original} ({kernels}): '
                 f'{len(result["cases"]) - failed - skipped}/{len(result["cases"]) - skipped} cases agree'
                 + (f' ({skipped} edge cases skipped: the original rejects them)' if skipped else '')
                 + f' [{result["runs"]["original"]} vs {result["runs"]["port"]}'
                 + (f', within {result["ulps"]} ulp' if result['ulps'] else ', bit-exact') + ']')
    if result.get('preflight'):
        lines.append(f'preflight: {result["preflight"]}')
    return '\n'.join(lines)


if __name__ == '__main__':
    raise SystemExit(main())
