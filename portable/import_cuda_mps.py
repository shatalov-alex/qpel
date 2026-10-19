"""Import the restricted CUDA subset through MPS MCP (never edit model XML)."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.request
from cuda_import import parse
from qpel_schema import is_identifier, is_block_size, MAX_BLOCK_SIZE

class MPS:
    def __init__(self, project, url):
        self.project=str(project.resolve());self.url=url;self.session=None
        self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'qpel-cuda-import','version':'1'}})
    def rpc(self,method,params):
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
        if self.session:headers['Mcp-Session-Id']=self.session
        req=urllib.request.Request(self.url,json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers)
        with urllib.request.urlopen(req,timeout=120) as response:
            self.session=response.headers.get('Mcp-Session-Id',self.session);raw=response.read().decode()
        if raw.startswith(('event:','data:')):raw='\n'.join(line[5:].strip() for line in raw.splitlines() if line.startswith('data:'))
        result=json.loads(raw)
        if 'error' in result:raise RuntimeError(result['error'])
        return result['result']
    def call(self,name,**kwargs):
        result=self.rpc('tools/call',{'name':'mps_mcp_'+name,'arguments':{'projectPath':self.project,**kwargs}})
        for item in result.get('content',[]):
            if item.get('type')=='text':
                data=json.loads(item['text'])
                if data.get('ok') is False or result.get('isError'):raise RuntimeError(data)
                return data.get('data',data)
        raise RuntimeError(result)
    def document(self,name,**kwargs):
        result=self.call(name,**kwargs)
        if isinstance(result,str) and result.startswith('/'):
            doc=json.loads(Path(result).read_text());return doc.get('data',doc) if isinstance(doc,dict) else doc
        return result

def node(c,p=None,k=None,r=None):
    d={'concept':'qpel.structure.'+c}
    if p:d['properties']=[{'name':a,'value':str(b).lower() if isinstance(b,bool) else str(b)} for a,b in p.items()]
    if k:d['children']=[{'role':a,'nodes':b if isinstance(b,list) else [b]} for a,b in k.items()]
    if r:d['references']=[{'role':a,'target':b} for a,b in r.items()]
    return d

def children(n,role):return next((x['nodes'] for x in n.get('children',[]) if x['role']==role),[])
def name(n):return next(x['value'] for x in n['properties'] if x['name']=='name')
def problems(x):
    if isinstance(x,dict):return list(x.get('problems',[]))+[e for k,v in x.items() if k!='problems' for e in problems(v)]
    if isinstance(x,list):return [e for v in x for e in problems(v)]
    return []

def import_model(mps, ir, module, model, block, journal):
    structure=mps.document('get_project_structure',startingPoint=model,includeRootNodes=True)
    existing={r['name'] for r in structure['rootNodes']}
    if module in existing or module+'_launch' in existing:raise ValueError('Target root already exists; choose a new module name')
    with tempfile.TemporaryDirectory(prefix='qpel-import-') as temp:
        serial=0
        def blueprint(obj):
            nonlocal serial
            serial+=1;p=Path(temp)/f'{serial}.json';p.write_text(json.dumps(obj));return str(p)
        def save():journal['path'].write_text(json.dumps({k:v for k,v in journal.items() if k!='path'},indent=2)+'\n')
        parameters=ir['parameters']
        root=node('GpuModule',{'name':module},k={'functions':node('Function',{'name':ir['kernel'],'isKernel':True},k={
            'parameters':[node('Parameter',{'name':p['name'],'binding':i,'isBuffer':True},k={'type':node(p['type'])}) for i,p in enumerate(parameters)],
            'resultType':node('VoidType'),'domain':node('Grid1D'),'body':node('Block')})})
        result=mps.call('insert_root_node_from_json',modelReference=model,json=blueprint(root))
        journal['module_reference']=result['reference'];save()
        data=mps.document('print_node',nodeReference=result['reference'],deep=True)
        f=children(data,'functions')[0];refs={name(p):p['reference'] for p in children(f,'parameters')}
        def ref(n):return node('Name',r={'declaration':refs[n]})
        def expr(e):
            if 'literal' in e:return node('Literal',{'value':str(e['literal'])+'u' if e['literal'] >= 2**31 else e['literal']})
            if 'grid' in e:return node('GridIndex')
            if 'member' in e:return node('Member',{'member':e['member']},k={'base':ref(ir['shape'])})
            if 'input' in e:return node('Index',k={'base':ref(e['input']),'index':node('GridIndex')})
            if 'op' in e:return node('Binary',{'operator':e['op']},k={'left':expr(e['left']),'right':expr(e['right'])})
            return node('Select',k={k:expr(e[k]) for k in ('condition','yes','no')})
        body=node('Block',k={'statements':[
            node('If',k={'condition':expr(ir['guard']),'thenBody':node('Block',k={'statements':node('Return')})}),
            node('Assign',k={'target':node('Index',k={'base':ref(ir['output']),'index':node('GridIndex')}),'value':expr(ir['value'])})]})
        mps.call('update_node',operation='SET',kind='CHILD',childNodeRef=children(f,'body')[0]['reference'],childJson=blueprint(body))
        plan=node('Pipeline',{'name':module+'_launch'},k={'resources':[node('Resource',{'name':p['name'],'kind':'shape' if p['name']==ir['shape'] else ('output' if p['name']==ir['output'] else 'input')},k={'type':node(p['type'])}) for p in parameters]},r={'module':journal['module_reference']})
        result=mps.call('insert_root_node_from_json',modelReference=model,json=blueprint(plan))
        journal['pipeline_reference']=result['reference'];save()
        data=mps.document('print_node',nodeReference=result['reference'],deep=True)
        resources={name(p):p['reference'] for p in children(data,'resources')}
        for n in resources:
            if n!=ir['shape']:mps.call('update_node',operation='SET',kind='REFERENCE',references=[[resources[n],'extent',resources[ir['shape']]]])
        stage=node('Dispatch',{'blockSize':block,'completionBarrier':True},k={'bindings':[node('Binding',r={'parameter':refs[p['name']],'resource':resources[p['name']]}) for p in parameters]},r={'kernel':f['reference'],'grid':resources[ir['shape']]})
        mps.call('update_node',operation='ADD',kind='CHILD',nodeReference=journal['pipeline_reference'],childRole='stages',childJson=blueprint(stage))
        for key in ['module_reference','pipeline_reference']:
            check=mps.document('check_root_node_problems',nodeReference=journal[key])
            if problems(check):raise RuntimeError(check)
        result=mps.call('alter_nodes',operation='MAKE',parameters=json.dumps({'models':[structure['reference']],'rebuild':True}))
        if not result.get('success') or any(d.get('kind')=='ERROR' for d in result.get('details',[])):raise RuntimeError(result)
        journal.update(status='complete',build=result);save()
        mps.call('open_node',nodeReference=journal['module_reference'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('--project',type=Path,required=True);p.add_argument('--module',required=True)
    p.add_argument('--model',default='qpel.sandbox');p.add_argument('--block',type=int,default=64)
    p.add_argument('--endpoint',default='http://127.0.0.1:64342/stream');p.add_argument('--report',type=Path,required=True)
    args=p.parse_args()
    if not is_identifier(args.module):p.error('Invalid module name')
    if not is_block_size(args.block):p.error(f'Block size must be 1..{MAX_BLOCK_SIZE}')
    if args.report.exists():p.error('Report already exists; inspect previous import before retrying')
    source=args.source.read_bytes();ir=parse(source.decode()) # Complete parse BEFORE contacting/mutating MPS.
    journal={'path':args.report,'status':'started','source':str(args.source.resolve()),'source_sha256':hashlib.sha256(source).hexdigest(),'module':args.module,'block_size':args.block,'ir':ir}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    try:import_model(MPS(args.project,args.endpoint),ir,args.module,args.model,args.block,journal)
    except Exception as e:
        journal.update(status='failed',error=str(e));args.report.write_text(json.dumps({k:v for k,v in journal.items() if k!='path'},indent=2)+'\n');raise
    print('PASS: CUDA imported into checked MPS roots; build completed. Report:',args.report)
