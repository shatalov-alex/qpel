"""qpel mcp: verify and translate served to AI agents over MCP (JSON-RPC on stdio)."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]
CUDA = HERE/'cuda_examples'


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class Server(unittest.TestCase):
    def exchange(self, *messages):
        """The server's responses, one per request, after sending every message on stdin."""
        text = ''.join(json.dumps(m) + '\n' for m in messages)
        done = subprocess.run([sys.executable, str(HERE/'qpel.py'), 'mcp'], input=text, capture_output=True,
                              text=True, timeout=600, cwd=HERE)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        return [json.loads(line) for line in done.stdout.splitlines()]  # stdout holds only protocol messages

    def test_handshake_and_tools(self):
        hello, tools, unknown = self.exchange(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '0'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'resources/list'})
        self.assertEqual(hello['result']['serverInfo']['name'], 'qpel')
        self.assertEqual(hello['result']['protocolVersion'], '2025-06-18')
        self.assertEqual([t['name'] for t in tools['result']['tools']], ['verify', 'translate'])
        self.assertEqual(unknown['error']['code'], -32601)

    def test_verify_reports_agreement_and_differences(self):
        with tempfile.TemporaryDirectory() as folder:
            wrong = Path(folder)/'port.cu'
            wrong.write_text((CUDA/'scale_offset.cu').read_text().replace('- 7;', '- 8;'))
            same, differs, broken = self.exchange(*[
                {'jsonrpc': '2.0', 'id': n, 'method': 'tools/call',
                 'params': {'name': 'verify', 'arguments': {'original': str(CUDA/'scale_offset.cu'), 'port': str(port)}}}
                for n, port in ((1, CUDA/'scale_offset.cu'), (2, wrong), (3, CUDA/'threshold.cu'))])
        self.assertTrue(same['result']['structuredContent']['passed'])
        self.assertFalse(same['result']['isError'])
        self.assertIn('cases agree', same['result']['content'][0]['text'])
        self.assertFalse(differs['result']['structuredContent']['passed'])
        self.assertIn('z[0]: original -7, port -8', differs['result']['content'][0]['text'])
        self.assertTrue(broken['result']['isError'])
        self.assertIn('has no kernel scale_offset', broken['result']['content'][0]['text'])

    def test_translate(self):
        with tempfile.TemporaryDirectory() as folder:
            (response,) = self.exchange({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'translate', 'arguments': {'source': str(CUDA/'scale_offset.cu'), 'out': str(Path(folder)/'out')}}})
            self.assertFalse(response['result']['isError'], response['result']['content'][0]['text'])
            self.assertTrue((Path(folder)/'out/package/generated/scale_offset.metal').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
