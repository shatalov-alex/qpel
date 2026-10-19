"""Fail-closed parser for the documented CUDA elementwise subset (not CUDA C++)."""
import argparse
import json
import re
from pathlib import Path

from qpel_schema import is_identifier

class UnsupportedCUDA(ValueError): pass

# Keep offsets for diagnostics; comments/includes are blanked, not deleted.
TOKEN = re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_][A-Za-z_0-9]*|[0-9]+[uU]?|>=|<=|==|!=|[{}()\[\],;.*+=?:<>&|^]|"C"')

def parse(source):
    return Parser(source).parse()

class Parser:
    def __init__(self, source):
        self.source=source;self.tokens=[];self.i=0
        clean=re.sub(r'(?m)^[ \t]*#include[ \t]+<cuda_runtime\.h>[ \t]*(?=\n|$)',lambda m:' '*len(m[0]),source)
        pos=0
        while pos<len(clean):
            m=TOKEN.match(clean,pos)
            if not m:self.fail('Unsupported token',pos)
            token=m[0]
            if not token.isspace() and not token.startswith(('//','/*')):self.tokens.append((token,pos))
            pos=m.end()
        self.tokens.append(('<EOF>',len(source)))
    def fail(self,message,pos=None):
        if pos is None:pos=self.tokens[self.i][1]
        line=self.source.count('\n',0,pos)+1;col=pos-self.source.rfind('\n',0,pos)
        raise UnsupportedCUDA(f'{line}:{col}: {message}')
    def peek(self):return self.tokens[self.i][0]
    def take(self,expected=None):
        value=self.peek()
        if expected is not None and value!=expected:self.fail(f'Expected {expected!r}; found {value!r}')
        if value=='<EOF>':self.fail('Unexpected end of source')
        self.i+=1;return value
    def accept(self,value):
        if self.peek()==value:self.take();return True
        return False
    def name(self):
        value=self.take()
        if not is_identifier(value):
            self.fail('Unsupported/reserved identifier: '+value)
        return value
    def expression(self,minimum=0):
        if self.accept('('):
            left=self.expression();self.take(')')
        elif re.fullmatch('[0-9]+[uU]?',self.peek()):
            raw=self.take();digits=raw.rstrip('uU')
            if (len(digits)>1 and digits.startswith('0')) or int(digits)>2**32-1:self.fail('Only decimal uint32 literals are supported')
            left={'literal':int(digits)}
        else:
            ident=self.name()
            if self.accept('['):
                self.take(self.index);self.take(']')
                if ident not in self.inputs:self.fail('Only read-only input[index] reads are supported')
                left={'input':ident}
            elif self.accept('.'):
                member=self.take()
                if self.scalar_count or ident!=self.shape or member not in ('x','y','z'):self.fail('Only shape.x/y/z members are supported')
                left={'member':member}
            elif ident==self.index:left={'grid':True}
            elif self.scalar_count and ident==self.shape:left={'count':True}
            else:self.fail('Unknown scalar: '+ident)
        precedence={'*':70,'>=':60,'>':60,'<=':60,'<':60,'==':50,'!=':50,'&':40,'^':30,'|':20}
        while self.peek() in precedence and precedence[self.peek()]>=minimum:
            op=self.take();right=self.expression(precedence[op]+1);left={'op':op,'left':left,'right':right}
        if minimum==0 and self.accept('?'):
            yes=self.expression();self.take(':');no=self.expression();left={'condition':left,'yes':yes,'no':no}
        return left
    def numeric_output(self,e):
        if 'literal' in e or 'input' in e:return True
        if e.get('op') in ('&','|','^'):
            return self.numeric_output(e['left']) and self.numeric_output(e['right'])
        if 'condition' in e:
            c=e['condition']
            return c.get('op') in ('>=','>','<=','<','==','!=') and self.numeric_output(c['left']) and self.numeric_output(c['right']) and self.numeric_output(e['yes']) and self.numeric_output(e['no'])
        return False
    def parse(self):
        if self.accept('extern'):self.take('"C"')
        self.take('__global__');self.take('void');kernel=self.name();self.take('(')
        parameters=[]
        while True:
            if self.accept('const'):
                self.take('unsigned');self.take('char');self.take('*');typ='ReadOnlyU8BufferType'
            elif self.accept('unsigned'):
                self.take('int');typ='U32BufferType' if self.accept('*') else 'ScalarCount'
            else:self.take('uint3');typ='Shape3DType'
            name=self.name();parameters.append({'name':name,'type':typ})
            if not self.accept(','):break
        self.take(')')
        names=[p['name'] for p in parameters]
        if len(set(names))!=len(names) or kernel in names or len(names)>31:self.fail('Duplicate names or too many parameters')
        self.inputs=[p['name'] for p in parameters if p['type']=='ReadOnlyU8BufferType']
        shapes=[p['name'] for p in parameters if p['type'] in ('Shape3DType','ScalarCount')];outputs=[p['name'] for p in parameters if p['type']=='U32BufferType']
        if not self.inputs or len(shapes)!=1 or len(outputs)!=1:self.fail('Require input buffer(s), exactly one uint3 or unsigned int count and one output buffer')
        self.shape=shapes[0];output=outputs[0]
        self.scalar_count=any(p['type']=='ScalarCount' for p in parameters)
        self.take('{');self.accept('const');self.take('unsigned');self.take('int');self.index=self.name()
        if self.index in names or self.index==kernel:self.fail('Index shadows a declaration')
        self.take('=')
        for token in ['blockIdx','.','x','*','blockDim','.','x','+','threadIdx','.','x',';']:self.take(token)
        self.take('if');self.take('(');guard=self.expression();self.take(')')
        def axes(e):
            return axes(e['left'])+axes(e['right']) if e.get('op')=='*' else [e.get('member')]
        correct_size=(guard.get('right')=={'count':True}) if self.scalar_count else axes(guard.get('right',{}))==['x','y','z']
        if guard.get('op')!='>=' or guard['left']!={'grid':True} or not correct_size:
            self.fail('Required bounds guard: index >= count or index >= shape.x * shape.y * shape.z')
        if self.scalar_count:
            # Host ABI adaptation: the count becomes the volume of a qpel shape.
            guard['right']={'op':'*','left':{'op':'*','left':{'member':'x'},'right':{'member':'y'}},'right':{'member':'z'}}
            for parameter in parameters:
                if parameter['type']=='ScalarCount':
                    parameter['source_type']='unsigned int';parameter['type']='Shape3DType'
        braces=self.accept('{');self.take('return');self.take(';')
        if braces:self.take('}')
        self.take(output);self.take('[');self.take(self.index);self.take(']');self.take('=')
        value=self.expression()
        if not self.numeric_output(value):self.fail('Output must be input[index], uint32 literal, bitwise operations, or numeric comparison/select')
        self.take(';');self.take('}')
        if self.peek()!='<EOF>':self.fail('Only one kernel is supported; trailing code rejected')
        return {'kernel':kernel,'parameters':parameters,'shape':self.shape,'output':output,'guard':guard,'value':value,'size_kind':'count' if self.scalar_count else 'shape'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);args=p.parse_args()
    try:print(json.dumps(parse(args.source.read_text()),indent=2))
    except UnsupportedCUDA as e:p.exit(2,str(e)+'\n')
