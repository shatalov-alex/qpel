"""Package the neutral source and launch plan from qpel_ir.Lowering for each backend.

Call through qpel.py build, which validates the IR first; this module does not.
"""
import hashlib
import json
from pathlib import Path
from generate_metal_host import generate, validate_resources
from generate_cuda_host import generate as generate_cuda
from qpel_schema import is_identifier, is_block_size

HERE = Path(__file__).resolve().parent
CONTRACT = b'// qpel numeric contract: u32-modulo-v1\n'

def identifier(value):
    if not is_identifier(value):
        raise ValueError('Invalid generated identifier: '+repr(value))
    return value

def launch_header(plan):
    if plan.get('schema') != 1: raise ValueError('Unsupported launch schema')
    resources = {identifier(r['name']):r for r in plan['resources']}
    if len(resources) != len(plan['resources']): raise ValueError('Duplicate resource')
    if not plan['stages']: raise ValueError('No dispatches')
    lines = ['// Generated from the checked MPS Pipeline. Requires QPEL_STAGE.']
    for stage in plan['stages']:
        kernel=identifier(stage['kernel']); grid=identifier(stage['grid']); block=stage['blockSize']
        if not is_block_size(block): raise ValueError('Invalid block size')
        if stage['completionBarrier'] is not True: raise ValueError('Stage completion is required')
        if grid not in resources or resources[grid]['kind'] != 'shape': raise ValueError('Invalid grid resource')
        args=[]
        for binding in stage['bindings']:
            identifier(binding['parameter']);name=identifier(binding['resource'])
            if name not in resources or name in args: raise ValueError('Unknown/aliased resource')
            resource=resources[name]
            if resource['kind'] not in ('shape','value') and resource['extent']!=grid: raise ValueError('Grid/extent mismatch')
            args.append(name)
        lines.append('QPEL_STAGE('+', '.join([kernel,grid,str(block)]+args)+')')
    return '\n'.join(lines)+'\n'

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
        half = b'#define QPEL_F16_ENABLED 1\n' if any(token in body for token in (b'QPEL_F16', b'QPEL_READ_F16', b'QPEL_WRITE_F16', b'QPEL_TO_F16')) else b''
        reduction = b'#define QPEL_SUM_F32_ENABLED 1\n' if b'QPEL_SUM_F32(' in body else b''
        group = b'#define QPEL_GROUP_SUM_F32_ENABLED 1\n' if b'QPEL_GROUP_SUM_F32(' in body else b''
        signed = b'#define QPEL_I32_ENABLED 1\n' if b'QPEL_I32' in body else b''
        values = b'#define QPEL_VALUE_ENABLED 1\n' if b'QPEL_VALUE(' in body else b''
        result = half + reduction + group + signed + values + b'// Generated backend package. Edit the MPS model or backend support.\n' + support + b'\n' + body
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
    (output / (module+'.qpel.h')).write_bytes(body)
    (output / 'pipeline.inc').write_text(dispatch)
    (output / 'pipeline.json').write_bytes(plan_bytes)
    contract = 'u32-modulo-v1' + (', i32-wrap-v1' if b'qpel numeric contract: i32-wrap-v1' in body else '')
    manifest = {'numeric_contract': contract, 'neutral_source': str(source.resolve()), 'neutral_source_sha256': hashlib.sha256(body).hexdigest(),
                'neutral_pipeline':str(plan_path.resolve()),'pipeline_sha256':hashlib.sha256(plan_bytes).hexdigest(),
                'metal_host_sha256':hashlib.sha256(host).hexdigest(),
                'cuda_host_sha256':hashlib.sha256(cuda_host).hexdigest(),
                'outputs': outputs, 'cuda_compiled': False, 'gpu_tested': False}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest
