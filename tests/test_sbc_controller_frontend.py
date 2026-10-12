"""CLI/MCP integration against a fake daemon. No serial or disk access."""
import json
from pathlib import Path
import selectors
import socket
import subprocess
import tempfile
import threading
import unittest

BINARY = Path(__file__).resolve().parents[1] / 'package/polis-sbc-controller/target/debug/polis-sbc-controller'


@unittest.skipUnless(BINARY.is_file(), 'build polis-sbc-controller native binaries first')
class SbcControllerFrontendTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'fake.sock')
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(self.path)
        self.listener.listen()
        self.listener.settimeout(5)
        self.requests = []
        self.failures = []

    def tearDown(self):
        self.listener.close()
        self.temp.cleanup()

    def fake_daemon(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(5)
                with connection.makefile('rb') as stream:
                    self.requests.append(json.loads(stream.readline()))
                connection.sendall(json.dumps({'ok': True, 'data': {'fixture': 'bench', 'simulated': True}, 'error': None}).encode()+b'\n')
        except Exception as error:
            self.failures.append(error)

    def args(self, *args):
        return [str(BINARY), '--socket', self.path, '--fixture', 'bench', *args]

    def test_cli_transmits_explicit_fixture_and_workflow_authorization(self):
        thread = threading.Thread(target=self.fake_daemon); thread.start()
        result = subprocess.run(self.args('run', '--image', '/explicit/candidate.img', '--sha256', 'a'*64,
                                        '--auto-recover', '--confirm-fixture', 'bench'),
                                check=True, capture_output=True, text=True, timeout=10)
        thread.join(5)
        self.assertFalse(self.failures)
        self.assertTrue(json.loads(result.stdout)['simulated'])
        request = self.requests[0]
        self.assertEqual(request['fixture'], 'bench')
        self.assertEqual(request['operation']['action'], 'workflow')
        self.assertTrue(request['operation']['auto_recover'])
        self.assertEqual(request['operation']['confirm_fixture'], 'bench')
        self.assertEqual(request['operation']['image']['path'], '/explicit/candidate.img')

    def test_recovery_usb_is_explicit_and_has_no_flash_parameters(self):
        thread = threading.Thread(target=self.fake_daemon); thread.start()
        subprocess.run(self.args('recovery-usb', '--confirm-fixture', 'bench'),
                       check=True, capture_output=True, text=True, timeout=10)
        thread.join(5)
        self.assertFalse(self.failures)
        self.assertEqual(self.requests[0]['operation'],
                         {'action': 'recovery_usb', 'confirm_fixture': 'bench'})

    def test_benchmark_transmits_count_and_confirmation(self):
        thread = threading.Thread(target=self.fake_daemon); thread.start()
        subprocess.run(self.args('benchmark', '--samples', '3', '--confirm-fixture', 'bench'),
                       check=True, capture_output=True, text=True, timeout=10)
        thread.join(5)
        self.assertFalse(self.failures)
        self.assertEqual(self.requests[0]['operation'],
                         {'action': 'benchmark', 'samples': 3, 'confirm_fixture': 'bench'})

    def test_diagnostics_uses_daemon_without_selecting_a_tty(self):
        thread = threading.Thread(target=self.fake_daemon); thread.start()
        subprocess.run(self.args('diagnostics'), check=True, capture_output=True, text=True, timeout=10)
        thread.join(5)
        self.assertFalse(self.failures)
        self.assertEqual(self.requests[0]['operation'], {'action': 'diagnostics'})

    def test_cli_requires_confirmation_and_paired_image_hash(self):
        for args in [('benchmark',), ('run',), ('recovery-usb',), ('run', '--image', '/image.img', '--confirm-fixture', 'bench')]:
            result = subprocess.run(self.args(*args), capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.requests)

    def test_mcp_lists_tools_and_calls_status_through_shared_daemon_api(self):
        thread = threading.Thread(target=self.fake_daemon); thread.start()
        process = subprocess.Popen(self.args('mcp'), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def send(value):
            process.stdin.write(json.dumps(value).encode()+b'\n'); process.stdin.flush()
        def response(wanted):
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    self.assertTrue(selector.select(5), 'MCP response deadline')
                    value = json.loads(process.stdout.readline())
                    if value.get('id') == wanted: return value
        try:
            send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'simulated-test','version':'1'}}})
            self.assertIn('result', response(1))
            send({'jsonrpc':'2.0','method':'notifications/initialized'})
            send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
            names = {t['name'] for t in response(2)['result']['tools']}
            self.assertTrue({'run','status','power','cycle','console_read','console_write','images','recovery_usb','diagnostics','benchmark'} <= names)
            send({'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'status','arguments':{}}})
            result = response(3)['result']
            self.assertFalse(result.get('isError', False))
            self.assertIn('simulated', result['content'][0]['text'])
            thread.join(5)
            self.assertFalse(self.failures)
            self.assertEqual(self.requests[0]['fixture'], 'bench')
            self.assertEqual(self.requests[0]['operation'], {'action':'status'})
            send({'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'run','arguments':{}}})
            rejected = response(4)
            self.assertTrue('error' in rejected or rejected.get('result', {}).get('isError'))
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
            for pipe in (process.stdin, process.stdout, process.stderr): pipe.close()
