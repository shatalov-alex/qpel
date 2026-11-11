"""Backend-neutral qpel graph and shared lowering for the current portable core.

Shared validation covers graph integrity, grammar, scalar types, lexical scope
and whole-resource pipeline effects, not numerical lowering, bounds or races.
"""
import json
from qpel_schema import validate_envelope, validate_structure
from qpel_types import validate_types, TypeChecker, NUMBERS
from qpel_pipeline import validate_pipeline
from qpel_bounds import validate_static_bounds

TYPES={'F16Type':'QPEL_F16','ReadOnlyF16BufferType':'QPEL_READ_F16','F16BufferType':'QPEL_WRITE_F16','F32Type':'QPEL_F32','ReadOnlyF32BufferType':'QPEL_READ_F32','F32BufferType':'QPEL_WRITE_F32','VoidType':'void','BoolType':'bool','U8Type':'QPEL_U8','U32Type':'QPEL_U32',
       'ReadOnlyU8BufferType':'QPEL_READ_U8','U32BufferType':'QPEL_WRITE_U32',
       'AtomicU32BufferType':'QPEL_ATOMIC_U32','Shape3DType':'QPEL_SHAPE'}
KINDS=set(TYPES)|set('GpuModule Function Parameter Block Variable Name Literal F32Literal F32Cast F16Cast F32Fma ReduceSumF32 WorkgroupSumF32 Binary Unary Index Member Call Select Assign Evaluate If While Return GridIndex Grid1D Minimum Maximum AtomicLoad AtomicStore AtomicAdd AtomicMin Pipeline Resource Dispatch Binding'.split())
COMMON_FEATURES={'integer_buffers','grid_1d','control_flow','device_relaxed_u32_atomics','float32','float32_fma','float16_storage','ordered_sum_f32','workgroup_sum_f32'}
PROFILES={'metal':COMMON_FEATURES,'cuda':COMMON_FEATURES}


def validate(program, backend=None):
    validate_envelope(program)
    if program.get('format')!='qpel.ir' or program.get('version')!=1 or program.get('semantics')!='qpel-portable-core-v1':
        raise ValueError('Unsupported qpel IR version/semantics')
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
        if n['kind'] in ('If','While'):required.add('control_flow')
        targets=list(n['links'].values())+[v for values in n['children'].values() for v in values]
        if any(t not in nodes for t in targets):raise ValueError('Dangling IR reference')
        if n['kind']=='Binary' and n['attributes'].get('operator') not in ('+','-','*','/','%','&','|','^','==','!=','<','>','<=','>=','&&','||'):
            raise ValueError('Unsupported binary operator')
        if n['kind']=='Unary' and n['attributes'].get('operator') not in ('!','-'):raise ValueError('Unsupported unary operator')
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
        if k in TYPES:return TYPES[k]
        if k=='GpuModule':return '// Generated from qpel IR. Do not edit.\n// qpel numeric contract: u32-modulo-v1\n'+joined('functions','\n')
        if k=='Function':
            kernel=a.get('isKernel',False);args=joined('parameters',', ')
            return ('QPEL_KERNEL ' if kernel else 'QPEL_DEVICE ')+e('resultType')+' '+a['name']+'('+args+(' QPEL_GRID_PARAM(qpel_index)' if kernel else '')+') {\n'+('QPEL_GRID_LOCAL(qpel_index)\n' if kernel else '')+e('body')+'\n}\n'
        if k=='Parameter':return 'QPEL_PARAM('+e('type')+', '+a['name']+', '+str(a.get('binding',0))+')' if a.get('isBuffer',False) else e('type')+' '+a['name']
        if k=='Name':return self.link(n,'declaration')['attributes']['name']
        if k=='GridIndex':return 'qpel_index'
        if k=='Literal':
            value = a['value']
            return value if value in ('true','false') else str(int(value.rstrip('uU'))) + 'u'
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
            if a['operator'] not in ('&&','||'):
                if self.expression_types[self.child(n,'left')['id']] in NUMBERS:left = '(QPEL_U32)('+left+')'
                if self.expression_types[self.child(n,'right')['id']] in NUMBERS:right = '(QPEL_U32)('+right+')'
            return '('+left+' '+a['operator']+' '+right+')'
        if k=='Unary':
            operand = e('operand')
            if a['operator']=='-' and self.expression_types[self.child(n,'operand')['id']] in NUMBERS:operand = '(QPEL_U32)('+operand+')'
            return '('+a['operator']+operand+')'
        if k=='Index':return e('base')+'['+e('index')+']'
        if k=='Member':return e('base')+'.'+a['member']
        if k=='Call':return self.link(n,'function')['attributes']['name']+'('+joined('arguments',', ')+')'
        if k in ('Minimum','Maximum'):
            left, right = e('left'), e('right')
            left, right = '(QPEL_U32)('+left+')', '(QPEL_U32)('+right+')'
            return ('min' if k=='Minimum' else 'max')+'('+left+', '+right+')'
        if k=='Select':return '('+e('condition')+' ? '+e('yes')+' : '+e('no')+')'
        if k=='Block':return '{\n'+joined('statements','')+' }'
        if k=='Variable':return e('type')+' '+a['name']+' = '+e('value')+';\n'
        if k=='Assign':return e('target')+' = '+e('value')+';\n'
        if k=='Evaluate':return e('value')+';\n'
        if k=='Return':return 'return '+(e('value') if self.children(n,'value') else '')+';\n'
        if k=='If':return 'if ('+e('condition')+') '+e('thenBody')+'\n'
        if k=='While':return 'while ('+e('condition')+') '+e('body')+'\n'
        if k in ('AtomicLoad','AtomicStore','AtomicAdd','AtomicMin'):
            macro={'AtomicLoad':'QPEL_LOAD','AtomicStore':'QPEL_STORE','AtomicAdd':'QPEL_ADD','AtomicMin':'QPEL_MIN'}[k]
            return macro+'('+e('address')+('' if k=='AtomicLoad' else ', '+e('value'))+')'
        raise ValueError('Not a source expression/statement: '+k)
    def plan(self):
        p=self.nodes[self.program['roots']['pipeline']];module=self.nodes[self.program['roots']['module']]
        resources=[]
        for r in self.children(p,'resources'):
            resources.append(dict(name=r['attributes']['name'],kind=r['attributes']['kind'],type=self.child(r,'type')['kind'],extent=self.link(r,'extent')['attributes']['name'] if 'extent' in r['links'] else None))
        stages=[]
        for s in self.children(p,'stages'):
            stages.append(dict(kernel=self.link(s,'kernel')['attributes']['name'],grid=self.link(s,'grid')['attributes']['name'],blockSize=s['attributes']['blockSize'],completionBarrier=s['attributes']['completionBarrier'],bindings=[dict(parameter=self.link(b,'parameter')['attributes']['name'],resource=self.link(b,'resource')['attributes']['name']) for b in self.children(s,'bindings')]))
        return dict(schema=1,module=module['attributes']['name'],resources=resources,stages=stages)
    def write(self,directory):
        directory.mkdir(parents=True,exist_ok=True);plan=self.plan();name=plan['module']
        source=directory/(name+'.qpel.h');source.write_text(self.emit(self.nodes[self.program['roots']['module']]))
        (directory/(name+'_launch.json')).write_text(json.dumps(plan,separators=(',',':'))+'\n')
        return source
