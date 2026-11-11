"""Backend-neutral qpel graph and shared lowering for the current portable core.

Shared validation covers graph integrity, grammar, scalar types, lexical scope
and whole-resource pipeline effects, not numerical lowering, bounds or races.
"""
import json
from qpel_schema import validate_envelope, validate_structure
from qpel_types import EXACT_MATH, validate_types, TypeChecker, LEGACY, term, is_buffer, element, access, is_signed, bits, unsigned_of, arithmetic, INTEGER_TERMS
from qpel_pipeline import validate_pipeline
from qpel_bounds import validate_static_bounds

# Type terms whose macros predate version 3 keep them, so earlier programs generate the same code.
LEGACY_MACROS={'void':'void','bool':'bool','u8':'QPEL_U8','u32':'QPEL_U32','i32':'QPEL_I32','f32':'QPEL_F32','f16':'QPEL_F16',
       'shape3':'QPEL_SHAPE','buffer<u8, read>':'QPEL_READ_U8','buffer<u32, write>':'QPEL_WRITE_U32',
       'buffer<u32, atomic>':'QPEL_ATOMIC_U32','buffer<i32, atomic>':'QPEL_ATOMIC_I32','buffer<i32, read>':'QPEL_READ_I32',
       'buffer<i32, write>':'QPEL_WRITE_I32','buffer<f32, read>':'QPEL_READ_F32','buffer<f32, write>':'QPEL_WRITE_F32',
       'buffer<f16, read>':'QPEL_READ_F16','buffer<f16, write>':'QPEL_WRITE_F16'}
TYPE_KINDS=set(LEGACY)|{'ScalarType','BufferType','StructType'}
KINDS=TYPE_KINDS|set('View StructDecl Field StructType Math Grid3D Groups GroupIndex LocalIndex GroupSize GroupCount ThreadIndex GpuModule Function Parameter Block Variable Name Literal I32Literal Constant Cast I32Cast U32Cast U8Cast For DoWhile Break Continue F32Literal F32Cast F16Cast F32Fma ReduceSumF32 WorkgroupSumF32 Binary Unary Index Member Call Select Assign Evaluate If While Return GridIndex Grid1D Minimum Maximum AtomicLoad AtomicStore AtomicAdd AtomicMin Pipeline Resource Dispatch Binding'.split())
COMMON_FEATURES={'integer_buffers','grid_1d','grid_3d','control_flow','int32','device_relaxed_u32_atomics','float32','float32_fma','float16_storage','ordered_sum_f32','workgroup_sum_f32','int8_16','int64','bfloat16_storage','groups','math_exact','math_bounded'}
PROFILES={'metal':COMMON_FEATURES,'cuda':COMMON_FEATURES}
SEMANTICS={1:'qpel-portable-core-v1',2:'qpel-portable-core-v2',3:'qpel-portable-core-v3'}
# Version 2 adds else, for/do/break/continue, signed 32-bit integers and their atomics,
# shifts, ~, conversion to u8, scalar arguments and 2D/3D kernels.
V2_KINDS={'Grid3D','ThreadIndex','For','DoWhile','Break','Continue','I32Type','ReadOnlyI32BufferType','I32BufferType','AtomicI32BufferType','I32Literal','I32Cast','U32Cast','U8Cast'}
# Version 3 writes types as terms (ScalarType, BufferType) and adds 8-, 16- and 64-bit
# integers, bf16 storage, writable u8 buffers, Cast and Constant.
V3_KINDS={'ScalarType','BufferType','Cast','Constant','Math','View','StructDecl','Field','StructType','Groups','GroupIndex','LocalIndex','GroupSize','GroupCount'}
def type_macro(t):
    """Backend macro spelling a type term."""
    if t in LEGACY_MACROS:return LEGACY_MACROS[t]
    if t.startswith('struct '):return t[len('struct '):]
    if is_buffer(t):return ('QPEL_READ(' if access(t)=='read' else 'QPEL_WRITE(')+type_macro(element(t))+')'
    return 'QPEL_'+t.upper()


def constant(scalar,value):
    """C spelling of an integer constant of a given type."""
    size,signed=bits(scalar),is_signed(scalar)
    if signed and value==-2**(size-1):
        text='(-'+str(2**(size-1)-1)+('ll' if size==64 else '')+' - 1)'
    else:
        text=str(value)+('' if signed else 'u')+('ll' if size==64 else '')
        if value<0:text='('+text+')'
    return text if scalar in ('u32','i32') else '(('+type_macro(scalar)+')'+text+')'


BINARY_OPERATORS={'+','-','*','/','%','&','|','^','==','!=','<','>','<=','>=','&&','||','<<','>>'}
UNARY_OPERATORS={'!','-','~'}
INT32_KINDS={'I32Type','ReadOnlyI32BufferType','I32BufferType','AtomicI32BufferType','I32Literal','I32Cast'}


def version_2_features(nodes):
    """Names of the version 2 constructs a program uses."""
    used=set()
    for n in nodes:
        k,a=n['kind'],n['attributes']
        if k in V2_KINDS:used.add(k)
        if k=='If' and n['children'].get('elseBody'):used.add('else')
        if k=='Binary' and a.get('operator') in ('<<','>>'):used.add('shift')
        if k=='Unary' and a.get('operator')=='~':used.add('~')
        if k=='Resource' and a.get('kind') in ('value','zeroed'):used.add(a['kind'])
        if k=='Dispatch' and ('blockHeight' in a or 'blockDepth' in a):used.add('block shape')
    return used


def version_3_features(nodes):
    return {n['kind'] for n in nodes if n['kind'] in V3_KINDS}


def minimum_version(program):
    return 3 if version_3_features(program['nodes']) else 2 if version_2_features(program['nodes']) else 1


def validate(program, backend=None):
    validate_envelope(program)
    if program.get('format')!='qpel.ir' or SEMANTICS.get(program.get('version'))!=program.get('semantics'):
        raise ValueError('Unsupported qpel IR version/semantics')
    if program['version']<2 and version_2_features(program['nodes']):
        raise ValueError('IR version 1 cannot use: '+', '.join(sorted(version_2_features(program['nodes'])))+'; declare version 2')
    if program['version']<3 and version_3_features(program['nodes']):
        raise ValueError(f"IR version {program['version']} cannot use: "+', '.join(sorted(version_3_features(program['nodes'])))+'; declare version 3')
    nodes={n['id']:n for n in program['nodes']}
    if len(nodes)!=len(program['nodes']):raise ValueError('Duplicate IR node id')
    required={'integer_buffers','grid_1d'}
    for n in nodes.values():
        if n['kind'] not in KINDS:raise ValueError('Unsupported core operation/type: '+n['kind'])
        if n['kind']=='WorkgroupSumF32':required.add('workgroup_sum_f32')
        if n['kind']=='ReduceSumF32':required.add('ordered_sum_f32')
        if 'F16' in n['kind']:required.add('float16_storage')
        if n['kind']=='F32Fma':required.add('float32_fma')
        if 'F32' in n['kind']:required.add('float32')
        if n['kind'].startswith('Atomic'):required.add('device_relaxed_u32_atomics')
        if n['kind'] in ('If','While','DoWhile','For'):required.add('control_flow')
        if n['kind'] in INT32_KINDS:required.add('int32')
        if n['kind']=='Grid3D':required.add('grid_3d')
        if n['kind']=='Groups':required.add('groups')
        if n['kind']=='Math':required.add('math_exact' if n['attributes']['function'] in EXACT_MATH else 'math_bounded')
        if n['kind'] in TYPE_KINDS|{'Cast','Constant'}:
            used=term(n) if n['kind'] in TYPE_KINDS else n['attributes'].get('to') or n['attributes'].get('scalar')
            item=element(used) if is_buffer(used) else used
            if item in ('u8','i8','u16','i16') and n['kind'] not in LEGACY:required.add('int8_16')
            if item in ('u64','i64'):required.add('int64')
            if item=='bf16':required.add('bfloat16_storage')
        targets=list(n['links'].values())+[v for values in n['children'].values() for v in values]
        if any(t not in nodes for t in targets):raise ValueError('Dangling IR reference')
        if n['kind']=='Binary' and n['attributes'].get('operator') not in BINARY_OPERATORS:
            raise ValueError('Unsupported binary operator')
        if n['kind']=='Unary' and n['attributes'].get('operator') not in UNARY_OPERATORS:raise ValueError('Unsupported unary operator')
    roots=program['roots']
    if set(roots)!= {'module','pipeline'} or any(r not in nodes for r in roots.values()) or nodes[roots['module']]['kind']!='GpuModule' or nodes[roots['pipeline']]['kind']!='Pipeline':raise ValueError('Invalid IR roots')
    if nodes[roots['pipeline']]['links'].get('module')!=roots['module']:raise ValueError('Pipeline targets a different module')
    seen=set();active=set()
    def visit(ref):
        if ref in active:raise ValueError('Containment cycle')
        if ref in seen:raise ValueError('Multiple containment parents')
        active.add(ref);seen.add(ref)
        for values in nodes[ref]['children'].values():
            for child in values:visit(child)
        active.remove(ref)
    for ref in roots.values():visit(ref)
    if seen!=set(nodes):raise ValueError('Unreachable IR nodes')
    validate_structure(nodes)
    validate_types(program)
    validate_pipeline(program)
    bounds = validate_static_bounds(program)
    if backend is not None:
        if backend not in PROFILES:raise ValueError('Unknown backend: '+backend)
        missing=required-PROFILES[backend]
        if missing:raise ValueError('Backend lacks capabilities: '+', '.join(sorted(missing)))
    return {'required_features':sorted(required),'backend':backend,'memory_checks':bounds,'scope':'Implemented capability/graph/grammar/scalar type/scope/pipeline checks; u32-modulo-v1 numeric lowering; no dynamic divisor, bounds, per-element initialization or race proof','gpu_tested':False}


class Lowering:
    def __init__(self,program):
        validate(program);self.program=program;self.nodes={n['id']:n for n in program['nodes']}
        checker = TypeChecker(program);checker.validate()
        self.expression_types = checker.expression_types
    def children(self,n,role):return [self.nodes[i] for i in n['children'].get(role,[])]
    def child(self,n,role):
        values=self.children(n,role)
        if len(values)!=1:raise ValueError('Required single child: '+n['kind']+'.'+role)
        return values[0]
    def link(self,n,role):return self.nodes[n['links'][role]]
    def emit(self,n):
        k=n['kind'];a=n['attributes']
        e=lambda role:self.emit(self.child(n,role))
        joined=lambda role,sep:sep.join(self.emit(x) for x in self.children(n,role))
        if k in TYPE_KINDS:return type_macro(term(n))
        if k=='GpuModule':
            signed='// qpel numeric contract: i32-wrap-v1\n' if any(x['kind'] in INT32_KINDS for x in self.nodes.values()) else ''
            widths='// qpel numeric contract: integers-v3\n' if version_3_features(self.nodes.values()) else ''
            floats='// qpel numeric contract: f32-strict-v1\n' if any(t=='f32' for t in self.expression_types.values()) else ''
            return '// Generated from qpel IR. Do not edit.\n// qpel numeric contract: u32-modulo-v1\n'+signed+widths+floats+joined('structs','')+joined('functions','\n')
        if k=='StructDecl':
            # Scalar fields in declaration order: CUDA and Metal lay them out the same (C alignment).
            return 'struct '+a['name']+' {\n'+''.join('  '+self.emit(self.child(f,'type'))+' '+f['attributes']['name']+';\n' for f in self.children(n,'fields'))+'};\n'
        if k=='Function':
            kernel=a.get('isKernel',False);args=joined('parameters',', ')
            if kernel and self.child(n,'domain')['kind']=='Grid3D':
                return 'QPEL_KERNEL '+e('resultType')+' '+a['name']+'('+args+' QPEL_GRID3D_PARAM) {\n'+e('body')+'\n}\n'
            if kernel and self.child(n,'domain')['kind']=='Groups':
                return 'QPEL_KERNEL '+e('resultType')+' '+a['name']+'('+args+' QPEL_GROUPS_PARAM) {\n'+e('body')+'\n}\n'
            return ('QPEL_KERNEL ' if kernel else 'QPEL_DEVICE ')+e('resultType')+' '+a['name']+'('+args+(' QPEL_GRID_PARAM(qpel_index)' if kernel else '')+') {\n'+('QPEL_GRID_LOCAL(qpel_index)\n' if kernel else '')+e('body')+'\n}\n'
        if k=='Parameter':
            if not a.get('isBuffer',False):return e('type')+' '+a['name']
            declared=term(self.child(n,'type'))
            macro='QPEL_PARAM(' if is_buffer(declared) or declared=='shape3' else 'QPEL_VALUE('  # structs travel as values
            return macro+e('type')+', '+a['name']+', '+str(a.get('binding',0))+')'
        if k=='Name':return self.link(n,'declaration')['attributes']['name']
        if k=='GridIndex':return 'qpel_index'
        if k=='ThreadIndex':
            return ('QPEL_GROUPS_THREAD_INDEX(' if self.domain_of(n)=='Groups' else 'QPEL_THREAD_INDEX(')+a['axis']+')'
        if k in ('GroupIndex','LocalIndex','GroupSize','GroupCount'):
            return {'GroupIndex':'QPEL_GROUP_INDEX(','LocalIndex':'QPEL_LOCAL_INDEX(','GroupSize':'QPEL_GROUP_SIZE(','GroupCount':'QPEL_GROUP_COUNT('}[k]+a['axis']+')'
        if k=='Literal':
            value = a['value']
            return value if value in ('true','false') else str(int(value.rstrip('uU'))) + 'u'
        if k=='I32Literal':return a['value']
        if k=='Constant':return constant(a['scalar'],int(a['value']))
        if k=='Math':return 'QPEL_MATH_'+a['function'].upper()+'('+joined('arguments',', ')+')'
        if k=='Cast':return self.cast(e('operand'),self.expression_types[self.child(n,'operand')['id']],a['to'])
        if k=='I32Cast':return '((QPEL_I32)('+e('operand')+'))'
        if k=='U32Cast':return '((QPEL_U32)('+e('operand')+'))'
        if k=='U8Cast':return '((QPEL_U8)('+e('operand')+'))'
        if k=='F32Literal':
            value = a['value']
            return value+('' if '.' in value or 'e' in value.lower() else '.0')+'f'
        if k=='WorkgroupSumF32':return 'QPEL_GROUP_SUM_F32('+e('value')+')'
        if k=='ReduceSumF32':return 'QPEL_SUM_F32('+e('buffer')+', '+e('start')+', '+e('count')+')'
        if k=='F32Fma':return 'QPEL_FMA_F32('+e('left')+', '+e('right')+', '+e('addend')+')'
        if k=='F16Cast':return 'QPEL_TO_F16('+e('operand')+')'
        if k=='F32Cast':return '(QPEL_F32)('+e('operand')+')'
        if k=='Binary':
            left, right = e('left'), e('right')
            op = a['operator']
            lt = self.expression_types[self.child(n,'left')['id']]
            rt = self.expression_types[self.child(n,'right')['id']]
            if op in ('<<','>>'):
                # The count is taken modulo the width: C leaves larger counts undefined.
                t = arithmetic(lt)
                count = '((QPEL_U32)('+right+') & '+str(bits(t)-1)+'u)'
                if is_signed(t):
                    if op == '<<':return '(('+type_macro(t)+')(('+type_macro(unsigned_of(t))+')('+left+') << '+count+'))'
                    return '(('+left+') >> '+count+')' if t == lt else '((('+type_macro(t)+')('+left+')) >> '+count+')'
                return '(('+type_macro(t)+')('+left+') '+op+' '+count+')'
            if op not in ('&&','||') and lt in INTEGER_TERMS and rt in INTEGER_TERMS:
                t = self.common(n)
                if is_signed(t):
                    s_, u_ = type_macro(t), type_macro(unsigned_of(t))
                    if op in ('+','-','*'):return '(('+s_+')(('+u_+')('+left+') '+op+' ('+u_+')('+right+')))'
                    if op in ('/','%'):
                        helper = ('QPEL_I32_DIV(' if op=='/' else 'QPEL_I32_REM(') if t=='i32' else ('QPEL_I64_DIV(' if op=='/' else 'QPEL_I64_REM(')
                        return helper+self.widen(left,lt,t)+', '+self.widen(right,rt,t)+')'
                    return '('+self.widen(left,lt,t)+' '+op+' '+self.widen(right,rt,t)+')'
                if t == 'u32':  # the version 1 spelling: every unsigned operand is converted
                    return '((QPEL_U32)('+left+') '+op+' (QPEL_U32)('+right+'))'
                return '(('+type_macro(t)+')('+left+') '+op+' ('+type_macro(t)+')('+right+'))'
            if lt == rt == 'f32' and op in ('+','-','*','/'):
                # Each float operation rounds on its own; only F32Fma fuses (f32-strict-v1).
                return {'+':'QPEL_FADD(','-':'QPEL_FSUB(','*':'QPEL_FMUL(','/':'QPEL_FDIV('}[op]+left+', '+right+')'
            if op not in ('&&','||') and rt in ('u8','u32'):
                right = '(QPEL_U32)('+right+')'  # a pointer offset
            return '('+left+' '+op+' '+right+')'
        if k=='Unary':
            operand = e('operand')
            operand_type = self.expression_types[self.child(n,'operand')['id']]
            if operand_type in INTEGER_TERMS and a['operator']!='!':
                t = arithmetic(operand_type)
                if is_signed(t):
                    if a['operator']=='-':return '(('+type_macro(t)+')(('+type_macro(unsigned_of(t))+')0u - ('+type_macro(unsigned_of(t))+')('+operand+')))' if t!='i32' else '((QPEL_I32)(0u - (QPEL_U32)('+operand+')))'
                    return '('+a['operator']+operand+')' if t == operand_type else '(('+type_macro(t)+')('+a['operator']+'('+type_macro(t)+')('+operand+')))'
                return '('+a['operator']+'('+type_macro(t)+')('+operand+'))'
            return '('+a['operator']+operand+')'
        if k=='Index':return e('base')+'['+e('index')+']'
        if k=='Member':return e('base')+'.'+a['member']
        if k=='Call':return self.link(n,'function')['attributes']['name']+'('+joined('arguments',', ')+')'
        if k in ('Minimum','Maximum'):
            left, right = e('left'), e('right')
            t = self.expression_types[n['id']]
            lt = self.expression_types[self.child(n,'left')['id']]
            rt = self.expression_types[self.child(n,'right')['id']]
            if t != 'i32' or lt != t or rt != t:
                left, right = '('+type_macro(t)+')('+left+')', '('+type_macro(t)+')('+right+')'
            return ('min' if k=='Minimum' else 'max')+'('+left+', '+right+')'
        if k=='Select':return '('+e('condition')+' ? '+e('yes')+' : '+e('no')+')'
        if k=='Block':return '{\n'+joined('statements','')+' }'
        if k=='Variable':
            if not hasattr(self,'read_names'):
                self.read_names={x['links']['declaration'] for x in self.nodes.values() if x['kind']=='Name'}
            unused='QPEL_UNUSED ' if n['id'] not in self.read_names else ''  # never read, e.g. after folded branches
            return unused+e('type')+' '+a['name']+(' = '+e('value') if self.children(n,'value') else '')+';\n'
        if k=='View':return '(('+type_macro(self.expression_types[n['id']])+')('+e('base')+'))'
        if k=='Assign':return e('target')+' = '+e('value')+';\n'
        if k=='Evaluate':return e('value')+';\n'
        if k=='Return':return 'return '+(e('value') if self.children(n,'value') else '')+';\n'
        if k=='If':return 'if ('+e('condition')+') '+e('thenBody')+(' else '+e('elseBody') if self.children(n,'elseBody') else '')+'\n'
        if k=='For':
            part = lambda role: self.emit(self.child(n,role)).strip().rstrip(';') if self.children(n,role) else ''
            return 'for ('+part('init')+'; '+part('condition')+'; '+part('step')+') '+e('body')+'\n'
        if k=='Break':return 'break;\n'
        if k=='Continue':return 'continue;\n'
        if k=='While':return 'while ('+e('condition')+') '+e('body')+'\n'
        if k=='DoWhile':return 'do '+e('body')+' while ('+e('condition')+');\n'
        if k in ('AtomicLoad','AtomicStore','AtomicAdd','AtomicMin'):
            macro={'AtomicLoad':'QPEL_LOAD','AtomicStore':'QPEL_STORE','AtomicAdd':'QPEL_ADD','AtomicMin':'QPEL_MIN'}[k]
            return macro+'('+e('address')+('' if k=='AtomicLoad' else ', '+e('value'))+')'
        raise ValueError('Not a source expression/statement: '+k)
    def domain_of(self,n):
        """Domain kind of the kernel containing node n."""
        if not hasattr(self,'parents'):
            self.parents={c:p for p in self.nodes.values() for values in p['children'].values() for c in values}
        while n['kind']!='Function':n=self.parents[n['id']]
        domains=self.children(n,'domain')
        return domains[0]['kind'] if domains else None
    def common(self,n):
        """The arithmetic type both integer operands of n are converted to."""
        from qpel_types import join
        return join(self.expression_types[self.child(n,'left')['id']],self.expression_types[self.child(n,'right')['id']])
    @staticmethod
    def widen(text,source,target):
        return text if source==target else '(('+type_macro(target)+')('+text+'))'
    @staticmethod
    def cast(text,source,target):
        if target in INTEGER_TERMS:
            if source=='f32':return 'QPEL_F32_TO_'+target.upper()+'('+text+')'  # saturating, see NUMERICS.md
            return '(('+type_macro(target)+')('+text+'))'
        if target=='f32':return '(QPEL_F32)('+text+')'
        if target=='f16':return 'QPEL_TO_F16('+text+')'
        if target=='bf16':return 'QPEL_TO_BF16('+text+')'
        raise ValueError('Unsupported conversion target '+target)
    def plan(self):
        p=self.nodes[self.program['roots']['pipeline']];module=self.nodes[self.program['roots']['module']]
        resources=[]
        for r in self.children(p,'resources'):
            resources.append(dict(name=r['attributes']['name'],kind=r['attributes']['kind'],type=term(self.child(r,'type')),extent=self.link(r,'extent')['attributes']['name'] if 'extent' in r['links'] else None))
        stages=[]
        for s in self.children(p,'stages'):
            stage=dict(kernel=self.link(s,'kernel')['attributes']['name'],grid=self.link(s,'grid')['attributes']['name'],blockSize=s['attributes']['blockSize'],completionBarrier=s['attributes']['completionBarrier'],bindings=[dict(parameter=self.link(b,'parameter')['attributes']['name'],resource=self.link(b,'resource')['attributes']['name']) for b in self.children(s,'bindings')])
            domain=self.child(self.link(s,'kernel'),'domain')['kind']
            if domain in ('Grid3D','Groups'):
                # A 3D stage runs one thread per shape element, in (x, y, z) blocks.
                stage.update(domain='grid3d',blockShape=[s['attributes']['blockSize'],s['attributes'].get('blockHeight',1),s['attributes'].get('blockDepth',1)])
            if domain=='Groups':
                # Explicit geometry: groups of blockShape threads, counted per axis from the grid shape.
                stage.update(domain='groups',groups=[s['attributes'].get('groups'+a,'1') for a in 'XYZ'])
            stages.append(stage)
        plan=dict(schema=2,module=module['attributes']['name'],resources=resources,stages=stages)
        structs={d['attributes']['name']:[[f['attributes']['name'],term(self.child(f,'type'))] for f in self.children(d,'fields')] for d in self.children(module,'structs')}
        if structs:plan['structs']=structs  # layouts, so hosts can pack struct arguments
        return plan
    def write(self,directory):
        directory.mkdir(parents=True,exist_ok=True);plan=self.plan();name=plan['module']
        source=directory/(name+'.qpel.h');source.write_text(self.emit(self.nodes[self.program['roots']['module']]))
        (directory/(name+'_launch.json')).write_text(json.dumps(plan,separators=(',',':'))+'\n')
        return source
