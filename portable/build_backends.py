"""Package the neutral source and launch plan from qpel_ir.Lowering for each backend.

Call through qpel.py build, which validates the IR first; this module does not.
"""
import hashlib
import json
from pathlib import Path
from generate_metal_host import generate, validate_resources
from generate_cuda_host import generate as generate_cuda
from qpel_schema import is_identifier, is_block_size, is_group_count
from qpel_types import normalize_plan

HERE = Path(__file__).resolve().parent
CONTRACT = b'// qpel numeric contract: u32-modulo-v1\n'

def identifier(value):
    if not is_identifier(value):
        raise ValueError('Invalid generated identifier: '+repr(value))
    return value

def launch_header(plan):
    plan = normalize_plan(plan)
    resources = {identifier(r['name']):r for r in plan['resources']}
    if len(resources) != len(plan['resources']): raise ValueError('Duplicate resource')
    if not plan['stages']: raise ValueError('No dispatches')
    lines = ['// Generated from the checked MPS Pipeline. Requires QPEL_STAGE.']
    for stage in plan['stages']:
        kernel=identifier(stage['kernel']); grid=identifier(stage['grid']); block=stage['blockSize']
        shape=stage.get('blockShape',[block,1,1])
        if stage.get('domain','grid1d') not in ('grid1d','grid3d','groups') or len(shape)!=3 or shape[0]!=block: raise ValueError('Invalid stage domain')
        if stage.get('domain')=='groups' and (len(stage.get('groups',()))!=3 or not all(is_group_count(g) for g in stage['groups'])): raise ValueError('Invalid group counts')
        if not all(is_block_size(d) for d in shape) or shape[0]*shape[1]*shape[2]>256: raise ValueError('Invalid block size')
        if stage['completionBarrier'] is not True: raise ValueError('Stage completion is required')
        if grid not in resources or resources[grid]['kind'] != 'shape': raise ValueError('Invalid grid resource')
        args=[]
        for binding in stage['bindings']:
            identifier(binding['parameter']);name=identifier(binding['resource'])
            if name not in resources or name in args: raise ValueError('Unknown/aliased resource')
            resource=resources[name]
            if resource['kind'] not in ('shape','value') and resource['extent']!=grid: raise ValueError('Grid/extent mismatch')
            args.append(name)
        if stage.get('domain')=='groups':
            counts=['('+g.replace('W','(('+grid+').x)').replace('H','(('+grid+').y)').replace('N','(('+grid+').z)')+')' for g in stage['groups']]
            lines.append('QPEL_STAGE_GROUPS('+', '.join([kernel,grid]+counts+[str(d) for d in shape]+args)+')')
        elif stage.get('domain')=='grid3d':
            lines.append('QPEL_STAGE3D('+', '.join([kernel,grid]+[str(d) for d in shape]+args)+')')
        else:
            lines.append('QPEL_STAGE('+', '.join([kernel,grid,str(block)]+args)+')')
    return '\n'.join(lines)+'\n'

# Program features (qpel_ir.Lowering.features, recorded in the launch plan) -> the support
# blocks of backends/*.h they need, in this order.
FEATURE_DEFINES = [('float16_storage', 'QPEL_F16_ENABLED'), ('ordered_sum_f32', 'QPEL_SUM_F32_ENABLED'),
                   ('workgroup_sum_f32', 'QPEL_GROUP_SUM_F32_ENABLED'), ('int32', 'QPEL_I32_ENABLED'),
                   ('scalar_arguments', 'QPEL_VALUE_ENABLED'), ('grid_3d', 'QPEL_GRID3D_ENABLED'),
                   (('int8_16', 'int64', 'typed_buffers'), 'QPEL_INT_WIDTHS_ENABLED'),
                   ('bfloat16_storage', 'QPEL_BF16_ENABLED'), ('float_to_int', 'QPEL_F32_TO_INT_ENABLED'),
                   ('groups', 'QPEL_GROUPS_ENABLED'), ('math', 'QPEL_MATH_ENABLED'),
                   ('atomic_cas', 'QPEL_CAS_ENABLED')]
# Backend profiles: what each target needs beyond its baseline for a feature.
METAL_STANDARD = {'bfloat16_storage': 'metal3.1'}  # bfloat needs Metal 3.1 (macOS 14); the baseline is metal3.0


def defines(features):
    lines = b''
    for feature, define in FEATURE_DEFINES:
        wanted = feature if isinstance(feature, tuple) else (feature,)
        if set(wanted) & set(features):
            lines += f'#define {define} 1\n'.encode()
    return lines


def build(source, output, plan_path=None):
    body = source.read_bytes()
    if CONTRACT not in body: raise ValueError('Source lacks the qpel numeric contract; build it with qpel.py')
    if not body.startswith(b'// Generated from qpel IR.'):
        raise ValueError('Expected a neutral source emitted from qpel IR; build it with qpel.py')
    plan_path = plan_path or source.with_name(source.name.removesuffix('.qpel.h') + '_launch.json')
    plan_bytes=plan_path.read_bytes();plan=json.loads(plan_bytes)
    module=identifier(plan['module']);dispatch=launch_header(plan)
    validate_resources(plan)
    output.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for backend, suffix in [('metal', 'metal'), ('cuda', 'cu')]:
        support = (HERE / 'backends' / (backend + '.h')).read_bytes()
        if 'features' not in plan:
            raise ValueError('Launch plan lacks the program features; build it with qpel.py')
        result = defines(plan['features']) + b'// Generated backend package. Edit the MPS model or backend support.\n' + support + b'\n' + body
        path = output / (module+'.'+suffix)
        path.write_bytes(result)
        if backend == 'metal':
            host = generate(plan, result).encode()
            (output / 'metal_host.py').write_bytes(host)
        if backend == 'cuda':
            cuda_host = generate_cuda(plan, result).encode()
            (output / 'cuda_host.py').write_bytes(cuda_host)
        outputs[backend] = {'file': path.name, 'sha256': hashlib.sha256(result).hexdigest(),
                            'support_sha256': hashlib.sha256(support).hexdigest()}
        standards = sorted({METAL_STANDARD[f] for f in plan['features'] if f in METAL_STANDARD})
        if backend == 'metal' and standards:
            outputs[backend]['standard'] = standards[-1]
    (output / (module+'.qpel.h')).write_bytes(body)
    (output / 'pipeline.inc').write_text(dispatch)
    (output / 'pipeline.json').write_bytes(plan_bytes)
    contract = 'u32-modulo-v1' + (', i32-wrap-v1' if b'qpel numeric contract: i32-wrap-v1' in body else '') + \
        (', integers-v3' if b'qpel numeric contract: integers-v3' in body else '') + \
        (', f32-strict-v1' if b'qpel numeric contract: f32-strict-v1' in body else '')
    manifest = {'numeric_contract': contract, 'features': plan['features'], 'neutral_source': str(source.resolve()), 'neutral_source_sha256': hashlib.sha256(body).hexdigest(),
                'neutral_pipeline':str(plan_path.resolve()),'pipeline_sha256':hashlib.sha256(plan_bytes).hexdigest(),
                'metal_host_sha256':hashlib.sha256(host).hexdigest(),
                'cuda_host_sha256':hashlib.sha256(cuda_host).hexdigest(),
                'outputs': outputs, 'cuda_compiled': False, 'gpu_tested': False}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
