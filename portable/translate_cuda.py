"""One-command restricted CUDA -> checked MPS model -> Metal/CUDA package."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

from cuda_import import parse
from qpel_schema import is_identifier, is_block_size, MAX_BLOCK_SIZE
from import_cuda_mps import MPS, import_model
from build_backends import build
from qpel_ir import export as export_ir, validate as validate_ir, Lowering

HERE=Path(__file__).resolve().parent

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, data):path.write_text(json.dumps(data,indent=2)+'\n')

def translate(source,project,module,out,block=64,endpoint='http://127.0.0.1:64342/stream',check=False):
    # Every source/argument validation runs before creating a directory or contacting MPS.
    raw=source.read_bytes();ir=parse(raw.decode())
    if not is_identifier(module):
        raise ValueError('Invalid module name')
    if not is_block_size(block):raise ValueError(f'Block size must be 1..{MAX_BLOCK_SIZE}')
    summary={'status':'source_accepted','source_sha256':hashlib.sha256(raw).hexdigest(),
             'module':module,'kernel':ir['kernel'],'size_kind':ir['size_kind'],'block_size':block,
             'project':str(project.resolve()),'ir':ir}
    if check:return summary
    generated_dir=project/'languages/qpel.sandbox/source_gen/qpel/sandbox'
    if not generated_dir.is_dir():raise ValueError('Expected existing qpel.sandbox source_gen layout; build project first')
    if out.exists():raise ValueError('Output directory already exists; choose a new path (no overwrite)')
    out.mkdir(parents=True,exist_ok=False)
    report=dict(summary,status='running',gpu_tested=False,nvcc_compiled=False)
    report_path=out/'translation-report.json';save(report_path,report)
    journal=dict(summary,path=out/'import-report.json',source=str(source.resolve()),status='started')
    try:
        mps=MPS(project,endpoint)
        import_model(mps,ir,module,'qpel.sandbox',block,journal)
        report.update(module_reference=journal['module_reference'],pipeline_reference=journal['pipeline_reference'])
        models={role:mps.document('print_node',nodeReference=journal[role+'_reference'],deep=True) for role in ['module','pipeline']}
        program=export_ir(models['module'],models['pipeline'])
        save(out/'program.qpel.json',program)
        report['numeric_contract']='u32-modulo-v1'
        report['backend_capabilities']={target:validate_ir(program,target) for target in ['metal','cuda']}
        neutral=Lowering(program);source_ir=neutral.write(out/'neutral')
        # Keep the native structural cross-check; numeric lowering deliberately differs.
        def tokens(text):return re.findall(r'[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]',re.sub(r'//[^\n]*','',text))
        legacy=Lowering(program, _native_compat=True)
        if tokens(legacy.emit(legacy.nodes[program['roots']['module']]))!=tokens((generated_dir/(module+'.qpel.h')).read_text()):
            raise ValueError('Shared IR lowering differs from native TextGen')
        if neutral.plan()!=json.loads((generated_dir/(module+'_launch.json')).read_text()):
            raise ValueError('Shared IR launch plan differs from native TextGen')
        # Only a complete backend package becomes generated/. Partial work stays in a temp directory.
        with tempfile.TemporaryDirectory(prefix='.package-',dir=out) as temp:
            stage=Path(temp)/'generated'
            manifest=build(source_ir,stage)
            if json.loads((stage/'pipeline.json').read_text())['module']!=module:raise ValueError('Generated plan/module mismatch')
            stage.rename(out/'generated')
        (out/'original.cu').write_bytes(raw)
        for role in ['module','pipeline']:
            save(out/(role+'-model.json'),models[role])
        inputs=[p['name'] for p in ir['parameters'] if p['type']=='ReadOnlyU8BufferType']
        shape_note='(count, 1, 1)' if ir['size_kind']=='count' else '(width, height, batch)'
        (out/'USAGE.md').write_text(
            '# Generated qpel package\n\nSource accepted, imported and built in MPS. GPU and nvcc validation have not run.\n\n'
            'Add this package directory to Python sys.path, then `from generated import metal_host`.\n\n'
            f'Call `metal_host.run(inputs, shapes)` with uint8 MPS tensors under input keys {inputs!r}, '
            f'and shapes={{{ir["shape"]!r}: {shape_note}}}. Output key: {ir["output"]!r}.\n\n'
            'All inputs must have the shape volume. Results are flat int32 tensors storing uint32 bits; reshape as needed. '
            'Nonempty volumes up to INT32_MAX are supported. Scalar count is adapted to shape volume, not passed through the original binary ABI.\n\n'
            'translation-report.json records artifact hashes and explicit validation status. import-report.json records IDE references. '
            'This package needs PyTorch with torch.mps.compile_shader and an Apple GPU. No MPS IDE is needed to execute the package.\n')
        report.update(status='generated',manifest=manifest,
                      artifact_sha256={str(f.relative_to(out)):digest(f) for f in sorted(out.rglob('*')) if f.is_file() and f!=report_path})
        save(report_path,report)
        return report
    except Exception as error:
        journal.update(status='failed',error=str(error))
        save(journal['path'],{k:v for k,v in journal.items() if k!='path'})
        report.update(status='failed',error=str(error))
        for key in ['module_reference','pipeline_reference']:
            if key in journal:report[key]=journal[key]
        save(report_path,report)
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('--module',required=True)
    p.add_argument('--project',type=Path,default=HERE.parent);p.add_argument('--out',type=Path)
    p.add_argument('--block',type=int,default=64);p.add_argument('--endpoint',default='http://127.0.0.1:64342/stream')
    p.add_argument('--check',action='store_true',help='Validate source only; no IDE/network/filesystem mutations')
    args=p.parse_args()
    if not args.check and args.out is None:p.error('--out is required unless --check is used')
    try:
        result=translate(args.source,args.project,args.module,args.out,args.block,args.endpoint,args.check)
        print(json.dumps(result if args.check else {'status':result['status'],'report':str(args.out/'translation-report.json'),'gpu_tested':False},indent=2))
    except (ValueError,RuntimeError,OSError) as error:p.exit(1,str(error)+'\n')
