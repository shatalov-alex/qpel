"""qpel as an MCP server, so an AI porting agent can check its work inside its own loop.

    qpel mcp        # speaks MCP (JSON-RPC 2.0, one message per line) on stdin and stdout

Register it with an MCP client, for example Claude Code:

    claude mcp add qpel -- qpel mcp

Tools:
- verify: run a port against its original on the same cases (qpel verify) and return the
  report: per case PASS/FAIL/SKIP with the first difference, the argument mapping and totals.
- translate: import a CUDA or Metal source and build qpel's CUDA and Metal from it
  (qpel translate), returning the generated files or the constructs qpel cannot import.

Paths are taken relative to the server's working directory. Nothing else is written: verify
works in a temporary folder, and translate writes only into the new `out` folder it is given.
"""
import contextlib
import json
from pathlib import Path
import sys

PROTOCOL = '2025-06-18'

VERIFY_SCHEMA = {
    'type': 'object',
    'properties': {
        'original': {'type': 'string', 'description': 'the reference source: CUDA (.cu) or Metal (.metal)'},
        'port': {'type': 'string', 'description': 'the port to check, in either language'},
        'kernels': {'type': 'array', 'items': {'type': 'string'}, 'description': "only these kernels of the original"},
        'map': {'type': 'object', 'additionalProperties': {'type': 'string'},
                'description': 'original kernel name -> port kernel name, where they differ'},
        'args': {'type': 'object', 'additionalProperties': {'type': 'string'},
                 'description': "port argument or struct field (args.ne0) -> expression of the original's scalar "
                                'arguments, W, H, N and numbers, for arguments that do not match by name or position'},
        'values': {'type': 'object', 'description': "original scalar -> 'volume', a number, or an expression of W, H, N"},
        'constants': {'type': 'object', 'description': 'Metal function constant -> value, for whichever side declares it'},
        'include': {'type': 'array', 'items': {'type': 'string'}, 'description': 'include folders for both sources'},
        'edges': {'type': 'boolean', 'default': True, 'description': 'also run cases with NaN, infinities, signed zeros, limits'},
        'nvidia': {'type': 'boolean', 'default': False, 'description': 'run CUDA on GPGPU-Sim (Docker) instead of the CPU simulator'},
        'metal': {'type': 'string', 'enum': ['preflight', 'cpu', 'gpu'], 'default': 'preflight',
                  'description': 'where Metal runs: preflight (the CPU simulator first, the Apple GPU only if every '
                                 'case finished there), cpu, or gpu. A Metal kernel that never ends cannot be '
                                 'stopped on an Apple GPU, so gpu is only for ports whose loops are known to end'},
        'dispatch': {'type': 'string', 'enum': ['threads', 'groups'], 'default': 'threads',
                     'description': 'launch a Metal port with an exact thread count, or with whole threadgroups'},
    },
    'required': ['original', 'port'],
}

TRANSLATE_SCHEMA = {
    'type': 'object',
    'properties': {
        'source': {'type': 'string', 'description': 'a CUDA (.cu) or Metal (.metal) source'},
        'out': {'type': 'string', 'description': 'a new folder for the package (must not exist)'},
        'module': {'type': 'string'},
        'include': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['source', 'out'],
}

TOOLS = [
    {'name': 'verify', 'inputSchema': VERIFY_SCHEMA,
     'description': 'Check that a port of GPU kernels (CUDA <-> Metal) behaves like its original: both run on the '
                    'same generated cases, outputs compared bit for bit (or within stated ulp bounds for math '
                    'functions). Returns PASS/FAIL per case with the first differing element. passed=false with '
                    'error means the port could not be run (signature does not map, does not compile).'},
    {'name': 'translate', 'inputSchema': TRANSLATE_SCHEMA,
     'description': "Translate a CUDA or Metal source with qpel: import it to qpel IR and build CUDA and Metal "
                    'kernels and hosts. Returns the generated files, or the unsupported constructs with positions.'},
]


def verify_tool(arguments):
    from differential import verify
    result = verify.verify(arguments['original'], arguments['port'], arguments.get('kernels'), arguments.get('map'),
                           [Path(f) for f in arguments.get('include', [])], arguments.get('constants'),
                           edges=arguments.get('edges', True), arguments=arguments.get('args'),
                           values=arguments.get('values'), nvidia=arguments.get('nvidia', False),
                           metal=arguments.get('metal', 'preflight'), dispatch=arguments.get('dispatch', 'threads'))
    return verify.summary(result, Path(arguments['original']).name, Path(arguments['port']).name), result, False


def translate_tool(arguments):
    from qpel import translate
    report = translate(Path(arguments['source']), Path(arguments['out']), module=arguments.get('module'),
                       include=[Path(f) for f in arguments.get('include', [])])
    return json.dumps(report, indent=1), report, False


HANDLERS = {'verify': verify_tool, 'translate': translate_tool}


def call(name, arguments):
    """(text, structured result, is_error) for one tool call."""
    from differential.verify import PortError
    if name not in HANDLERS:
        raise LookupError(name)
    try:
        return HANDLERS[name](arguments or {})
    except (PortError, ValueError, RuntimeError, OSError, KeyError) as error:
        message = f'{type(error).__name__}: {error}' if isinstance(error, KeyError) else str(error)
        return message, {'passed': False, 'error': message}, True


def handle(message):
    """The response to one JSON-RPC message, or None for a notification."""
    method, ident = message.get('method'), message.get('id')
    if ident is None:  # notifications (initialized, cancelled) need no answer
        return None
    if method == 'initialize':
        from qpel import VERSION
        requested = (message.get('params') or {}).get('protocolVersion')
        result = {'protocolVersion': requested or PROTOCOL, 'capabilities': {'tools': {'listChanged': False}},
                  'serverInfo': {'name': 'qpel', 'version': VERSION},
                  'instructions': 'qpel checks GPU kernel ports between CUDA and Metal. Call verify after writing '
                                  'or changing a port; fix the first difference it reports and call it again.'}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        params = message.get('params') or {}
        try:
            text, structured, failed = call(params.get('name'), params.get('arguments'))
        except LookupError:
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': f'unknown tool {params.get("name")}'}}
        result = {'content': [{'type': 'text', 'text': text}], 'structuredContent': structured, 'isError': failed}
    else:
        return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': f'unknown method {method}'}}
    return {'jsonrpc': '2.0', 'id': ident, 'result': result}


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(error)}}
        else:
            with contextlib.redirect_stdout(sys.stderr):  # stdout carries only protocol messages
                response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response) + '\n')
            stdout.flush()


if __name__ == '__main__':
    serve()
