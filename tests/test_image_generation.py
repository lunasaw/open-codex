import json
import os
import base64
import shutil
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_console import FakeHost, call, payload
from codex_agent.config import AgentConfig
from codex_agent.console.server import ConsoleApp
from codex_agent.events import EventLog
from codex_agent.image_generation import DEFAULTS, ImageGenerationSettings, api_base, private_write, request_headers


class ImageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / 'chat-key'
        private_write(self.key, 'fixture-secret')
        self.config = SimpleNamespace(state_dir=self.root / 'state', codex_home=self.root / 'home',
                                      raw={'codex': {'model_api_key_file': str(self.key)}})
        self.settings = ImageGenerationSettings(self.config, editable=True)
        # Keep mutable test fixtures away from the immutable project skill bundle.
        self.settings.script = self.root / 'source-skill/scripts/_image_gen_original.py'
        self.settings.script.parent.mkdir(parents=True)
        self.settings.script.write_text('''import os, sys
def _create_client(): pass
def _create_async_client(): pass
def main():
    print(os.environ['OPENAI_API_KEY'])
    print(sys.argv[1:])
    return 0
if __name__ == '__main__': raise SystemExit(main())
''')
        self.source = self.settings.script.parent.parent
        private_write(self.source / 'SKILL.md', '---\nname: "imagegen-api"\ndescription: "Generate images."\n---\n\n'
                      '<!-- codex-agent:image-generation-settings -->\n'
                      '# Complete workflow\nCollect input roles, preserve constraints, inspect and iterate.\n')
        shutil.copyfile(Path(__file__).parents[1] / 'src/codex_agent/skills/imagegen-api/scripts/image_gen.py',
                        self.source / 'scripts/image_gen.py')
        private_write(self.source / 'references/prompting.md', 'Keep user constraints.\n')
        private_write(self.source / 'assets/icon.png', b'\x89PNG\r\n\x1a\n')
        private_write(self.source / 'agents/openai.yaml', 'interface:\n  display_name: "Image Gen API"\n'
                      '  default_prompt: "Use $imagegen-api."\npolicy:\n  allow_implicit_invocation: false\n')
        self.native = {'model_provider': 'openai', 'openai_base_url': 'https://gateway.example/v1'}

    def body(self, **changes):
        public = self.settings.public(self.native)
        return {**{key: public[key] for key in DEFAULTS}, 'revision': public['revision'], **changes}

    def test_current_key_is_referenced_and_never_returned_or_in_skill(self):
        saved = self.settings.save(self.body(enabled=True), self.native)
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['currentBaseUrl'], self.native['openai_base_url'])
        self.assertNotIn('fixture-secret', json.dumps(saved) + self.settings.skill.read_text())
        profile = json.loads(self.settings.path.read_text())
        self.assertEqual(profile['keyFile'], str(self.key))
        self.assertNotIn('apiKey', profile)
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)
        # The generated entrypoint must work in a native shell with no PYTHONPATH.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        runner = Path(__file__).parents[1] / 'src/codex_agent/image_generation_cli.py'
        result = subprocess.run([sys.executable, str(runner), '--profile', str(self.settings.path),
                                 '--prompt', 'fixture', '--out', 'fixture.png', '--dry-run'],
                                cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('gpt-image-2', result.stdout)
        self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        self.settings.save(self.body(enabled=False), self.native)
        self.assertFalse(self.settings.skill.exists())
        result = subprocess.run([sys.executable, str(runner), '--profile', str(self.settings.path),
                                 '--prompt', 'fixture', '--out', 'fixture.png'], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_complete_skill_and_resources_preserved_with_configured_entrypoint(self):
        original = {path.relative_to(self.source): path.read_bytes()
                    for path in self.source.rglob('*') if path.is_file()}
        self.settings.save(self.body(enabled=True), self.native)
        target = self.settings.skill.parent
        body = (self.source / 'SKILL.md').read_text().split('\n---\n', 1)[1].lstrip('\n')
        self.assertTrue(self.settings.skill.read_text().endswith(body))
        self.assertEqual(self.settings.api_script.read_bytes(), original[Path('scripts/_image_gen_original.py')])
        self.assertEqual((target / 'scripts/image_gen.py').read_bytes(), original[Path('scripts/image_gen.py')])
        binding = json.loads((target / 'scripts/_host_binding.json').read_text())
        self.assertEqual(binding['cli'], str(Path(__file__).parents[1] / 'src/codex_agent/image_generation_cli.py'))
        self.assertEqual(binding['profile'], str(self.settings.runtime_path))
        for name in ('references/prompting.md', 'assets/icon.png'):
            self.assertEqual((target / name).read_bytes(), original[Path(name)])
        metadata = (target / 'agents/openai.yaml').read_text()
        self.assertIn('Use $imagegen-api.', metadata)
        self.assertIn('allow_implicit_invocation: false', metadata)
        self.assertEqual(original, {path.relative_to(self.source): path.read_bytes()
                                   for path in self.source.rglob('*') if path.is_file()})
        # The copied public entrypoint accepts original CLI commands and flags.
        command = [sys.executable, str(target / 'scripts/image_gen.py'), 'generate-batch',
                   '--input', 'jobs.jsonl', '--out-dir', 'images', '--concurrency', '2', '--dry-run']
        result = subprocess.run(command, cwd=self.root, env={'PATH': '/usr/bin:/bin'},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('generate-batch', result.stdout)
        self.assertIn('jobs.jsonl', result.stdout)
        self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        private_write(self.source / 'references/prompting.md', 'Updated upstream guidance.\n')
        self.settings.initialize(self.native)
        self.assertEqual((target / 'references/prompting.md').read_bytes(),
                         (self.source / 'references/prompting.md').read_bytes())

    def test_custom_key_preserved_only_for_same_endpoint(self):
        self.settings.save(self.body(connection='custom', baseUrl='https://images.example/v1', apiKey='custom-secret'), self.native)
        self.assertTrue(self.settings.public(self.native)['keyConfigured'])
        self.assertNotIn('custom-secret', json.dumps(self.settings.public(self.native)))
        self.settings.save(self.body(model='gpt-image-1.5'), self.native)
        self.assertEqual(self.settings.credential(self.settings.read()), 'custom-secret')
        with self.assertRaises(ValueError):
            self.settings.save(self.body(baseUrl='https://different.example/v1'), self.native)

    def test_header_defaults_web_override_model_changes_and_validation(self):
        headers = {'x-higress-llm-model': '{model}', 'X-Gateway-Option': 'fixed'}
        self.settings = ImageGenerationSettings(self.file_config(headers=headers), editable=True)
        self.settings.initialize(self.native)
        self.assertEqual(self.settings.public(self.native)['headers'], headers)
        self.assertEqual(json.loads(self.settings.runtime_path.read_text())['headers'], headers)
        self.settings.save(self.body(model='gpt-image-2.5-flare'), self.native)
        self.assertEqual(request_headers(self.settings.read())['x-higress-llm-model'], 'gpt-image-2.5-flare')
        self.settings.save(self.body(model='gpt-image-2.5-sunburst'), self.native)
        self.assertEqual(request_headers(self.settings.read())['x-higress-llm-model'], 'gpt-image-2.5-sunburst')
        for invalid in ([], None, {'bad name': 'x'}, {'X-Route': 'a\r\nb'}, {'X-Route': 1},
                        {'Authorization': 'secret'}, {'Content-Type': 'application/json'},
                        {'Host': 'other.example'}, {'Connection': 'close'}, {'X-Route': 'x', 'x-route': 'y'},
                        {'X-Route': '图像'}, {'X-Route': 'a' * 4097},
                        {f'X-{i}': 'x' for i in range(33)}, {f'X-{i}': 'x' * 4096 for i in range(5)}):
            with self.subTest(headers=invalid), self.assertRaises(ValueError):
                self.settings.prepare(self.body(headers=invalid), self.native)
            with self.subTest(file_headers=invalid), self.assertRaises(ValueError):
                self.file_config(headers=invalid)
        self.settings.save(self.body(headers={}), self.native)
        self.assertEqual(request_headers(self.settings.read()), {})

    def test_connection_probes_receive_resolved_headers(self):
        headers = {'x-higress-llm-model': '{model}'}
        with patch('codex_agent.image_generation.request_json', side_effect=[(404, {}), (400, {})]) as request:
            self.settings.check(self.body(model='gpt-image-2.5-flare', headers=headers), self.native)
        for args in request.call_args_list:
            self.assertEqual(args.kwargs['headers'], {'x-higress-llm-model': 'gpt-image-2.5-flare'})

    def test_headers_reach_json_and_multipart_sdk_requests_without_leaking(self):
        requests = []

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers, b''))
                self.respond({'data': []})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                requests.append((self.path, self.headers, raw))
                self.respond({'data': []})

            def respond(self, body):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}/v1'
        self.settings.script.write_text('''import argparse, asyncio, os, sys
from openai import OpenAI, AsyncOpenAI
def _create_client(): return OpenAI()
def _create_async_client(): return AsyncOpenAI()
async def perform():
    parser = argparse.ArgumentParser()
    parser.add_argument('command')
    parser.add_argument('--model')
    parser.add_argument('--image')
    args, _ = parser.parse_known_args()
    client = _create_async_client() if os.environ.get('FIXTURE_ASYNC') else _create_client()
    if args.command == 'edit':
        with open(args.image, 'rb') as image:
            result = client.images.edit(model=args.model, prompt='edit', image=image)
            if os.environ.get('FIXTURE_ASYNC'): await result
    else:
        result = client.images.generate(model=args.model, prompt='generate')
        if os.environ.get('FIXTURE_ASYNC'): await result
    if os.environ.get('FIXTURE_ASYNC'): await client.close()
    else: client.close()
    print('header-private-value ' + os.environ['OPENAI_API_KEY'])
def main():
    asyncio.run(perform())
    return 0
if __name__ == '__main__': raise SystemExit(main())
''')
        headers = {'x-higress-llm-model': '{model}', 'X-Option': 'header-private-value'}
        self.settings.save(self.body(enabled=True, connection='custom', baseUrl=base,
                                     apiKey='custom-secret', headers=headers, model='gpt-image-2.5-flare'), self.native)
        self.settings.check(self.body(), self.native)
        image = self.root / 'input.png'
        image.write_bytes(b'fixture-image-bytes')
        runner = Path(__file__).parents[1] / 'src/codex_agent/image_generation_cli.py'
        for asynchronous in (False, True):
            for editing in (False, True):
                command = [sys.executable, str(runner), '--profile', str(self.settings.runtime_path),
                           '--prompt', 'fixture', '--out', str(self.root / 'out.png')]
                if editing:
                    command.extend(['--image', str(image)])
                result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                        env={**os.environ, 'FIXTURE_ASYNC': '1' if asynchronous else ''})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('custom-secret', result.stdout + result.stderr)
                self.assertNotIn('header-private-value', result.stdout + result.stderr)
        self.assertEqual(len(requests), 6)
        for path, wire_headers, raw in requests:
            self.assertEqual(wire_headers['x-higress-llm-model'], 'gpt-image-2.5-flare')
            self.assertEqual(wire_headers['Authorization'], 'Bearer custom-secret')
            self.assertEqual(wire_headers['X-Option'], 'header-private-value')
            if path.endswith('/edits'):
                self.assertIn('multipart/form-data; boundary=', wire_headers['Content-Type'])
                message = BytesParser(policy=default).parsebytes(
                    ('Content-Type: ' + wire_headers['Content-Type'] + '\r\n\r\n').encode() + raw)
                parts = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                         for part in message.iter_parts()}
                self.assertEqual(parts['model'], b'gpt-image-2.5-flare')
                self.assertIn(b'fixture-image-bytes', parts.values())
            elif raw:
                self.assertEqual(json.loads(raw)['model'], 'gpt-image-2.5-flare')

    def file_config(self, **changes):
        binary = self.root / 'codex-fixture'
        binary.write_text('fixture runtime\n')
        return AgentConfig.from_dict({
            'schema_version': 'codex-agent/v2',
            'paths': {'codex_home': 'home', 'runtime_dir': 'runtime', 'state_dir': 'state', 'workspace_dir': 'workspace'},
            'codex': {'binary': str(binary), 'runtime_version': 'codex-cli 0.163.0'},
            'image_generation': {'enabled': True, 'connection': 'custom', 'base_url': 'http://images.example/v1/',
                                 'api_key_file': 'chat-key', **changes},
        }, self.root)

    def test_original_cli_generation_edit_and_batch_through_configured_gateway(self):
        bundle = Path(__file__).parents[1] / 'src/codex_agent/skills/imagegen-api'
        shutil.copytree(bundle, self.source, dirs_exist_ok=True)
        self.settings.script = self.source / 'scripts/_image_gen_original.py'
        requests = []
        encoded = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='

        class Gateway(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers['Content-Length']))
                if self.path.endswith('/edits'):
                    message = BytesParser(policy=default).parsebytes(
                        ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + raw)
                    parts = list(message.iter_parts())
                    fields = {p.get_param('name', header='content-disposition'): p.get_payload(decode=True)
                              for p in parts}
                    body = {k: v.decode() for k, v in fields.items() if k not in {'image[]', 'mask'}}
                    body['files'] = [p.get_payload(decode=True) for p in parts if p.get_filename()]
                else:
                    body = json.loads(raw)
                requests.append((self.path, self.headers, body))
                data = {'data': [{'b64_json': encoded} for _ in range(int(body.get('n', 1)))]}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.settings.save(self.body(enabled=True, connection='custom',
                                     baseUrl=f'http://127.0.0.1:{server.server_port}/v1',
                                     apiKey='fixture-secret', headers={'X-Route': '{model}'}), self.native)
        entry = self.settings.skill.parent / 'scripts/image_gen.py'

        def invoke(*arguments, success=True):
            result = subprocess.run([sys.executable, str(entry), *arguments], cwd=self.root,
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertNotIn('fixture-secret', result.stdout + result.stderr)
            return result

        prompt = self.root / 'prompt.txt'
        prompt.write_text('A white cat.\n')
        invoke('generate', '--prompt-file', str(prompt), '--out', 'preview.png', '--dry-run')
        self.assertFalse(requests)
        invoke('generate', '--prompt-file', str(prompt), '--n', '2', '--out', 'variant.png',
               '--quality', 'high', '--size', '2048x1152', '--constraints', 'No text.')
        outputs = list(self.root.glob('variant*.png'))
        self.assertEqual(len(outputs), 2)
        self.assertTrue(all(p.read_bytes() == base64.b64decode(encoded) for p in outputs))
        self.assertEqual(requests[-1][2]['quality'], 'high')
        self.assertEqual(requests[-1][2]['size'], '2048x1152')
        self.assertIn('No text.', requests[-1][2]['prompt'])
        invoke('edit', '--image', str(outputs[0]), '--image', str(outputs[1]), '--mask', str(outputs[0]),
               '--prompt', 'Change only the background; preserve the cat.', '--out', 'edited.png')
        self.assertEqual(len(requests[-1][2]['files']), 3)
        self.assertEqual((self.root / 'edited.png').read_bytes(), base64.b64decode(encoded))
        jobs = self.root / 'jobs.jsonl'
        jobs.write_text(json.dumps({'prompt': 'Cat', 'out': 'cat.png'}) + '\n' +
                        json.dumps({'prompt': 'Dog', 'model': 'gpt-image-1.5', 'out': 'dog.png'}) + '\n')
        invoke('generate-batch', '--input', str(jobs), '--out-dir', 'batch', '--concurrency', '2')
        self.assertEqual(len(list((self.root / 'batch').glob('*.png'))), 2)
        self.assertEqual({body['model'] for _, _, body in requests[-2:]}, {'gpt-image-2', 'gpt-image-1.5'})
        for _, headers, body in requests:
            self.assertEqual(headers['X-Route'], body['model'])
            self.assertEqual(headers['Authorization'], 'Bearer fixture-secret')
        # Preserve upstream protection for existing assets and model limitations.
        (self.root / 'protected.png').write_bytes(b'existing user asset')
        invoke('generate', '--prompt', 'Cat', '--out', 'protected.png', success=False)
        self.assertEqual((self.root / 'protected.png').read_bytes(), b'existing user asset')
        count = len(requests)
        invoke('generate', '--prompt', 'Cat', '--background', 'transparent', success=False)
        self.assertEqual(len(requests), count)

    def test_file_defaults_activate_on_startup_and_preserve_web_overrides(self):
        config = self.file_config(quality='high')
        self.settings = ImageGenerationSettings(config)
        self.assertEqual(config.image_generation['keyFile'], str(self.key))
        public = self.settings.public(self.native)
        self.assertTrue(public['enabled'])
        self.assertTrue(public['keyConfigured'])
        self.assertFalse(public['editable'])
        self.assertNotIn('fixture-secret', json.dumps(public))
        self.assertTrue(self.settings.initialize(self.native))
        self.assertFalse(self.settings.path.exists())
        self.assertTrue(self.settings.skill.is_file())
        profile = json.loads(self.settings.runtime_path.read_text())
        self.assertEqual(profile['baseUrl'], 'http://images.example/v1')
        self.assertNotIn('apiKey', profile)
        self.assertNotIn('fixture-secret', self.settings.runtime_path.read_text() + self.settings.skill.read_text())
        self.assertEqual(self.settings.runtime_path.stat().st_mode & 0o777, 0o600)
        runner = Path(__file__).parents[1] / 'src/codex_agent/image_generation_cli.py'
        command = [sys.executable, str(runner), '--profile', str(self.settings.runtime_path),
                   '--prompt', 'fixture', '--out', 'fixture.png', '--dry-run']
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        # File defaults are reapplied on restart while there is no web override.
        self.settings = ImageGenerationSettings(self.file_config(model='gpt-image-1.5'))
        self.settings.initialize(self.native)
        self.assertEqual(json.loads(self.settings.runtime_path.read_text())['model'], 'gpt-image-1.5')
        self.settings.editable = True
        self.settings.save(self.body(quality='low'), self.native)
        self.assertEqual(self.settings.credential(self.settings.read()), 'fixture-secret')
        self.settings = ImageGenerationSettings(self.file_config(model='gpt-image-2', quality='high'), editable=True)
        self.settings.initialize(self.native)
        self.assertEqual(self.settings.public(self.native)['quality'], 'low')
        self.assertEqual(self.settings.public(self.native)['model'], 'gpt-image-1.5')
        # An endpoint change must never inherit the file credential.
        with self.assertRaisesRegex(ValueError, 'Key'):
            self.settings.save(self.body(baseUrl='https://other.example/v1'), self.native)
        self.settings.save(self.body(apiKey='replacement-secret'), self.native)
        self.assertNotIn('keyFile', self.settings.read())
        self.assertEqual(self.settings.credential(self.settings.read()), 'replacement-secret')
        self.settings.save(self.body(enabled=False), self.native)
        self.assertFalse(self.settings.skill.exists())
        self.assertFalse(json.loads(self.settings.runtime_path.read_text())['enabled'])
        stale = json.loads(self.settings.runtime_path.read_text())
        stale['enabled'] = True
        private_write(self.settings.runtime_path, json.dumps(stale))
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_file_defaults_fail_early_for_invalid_or_unprotected_credentials(self):
        for changes in ({'enabled': 'true'}, {'model': 'chat-model'}, {'quality': []},
                        {'api_key': 'secret'}, {'api_key_file': ''}, {'api_key_file': '../key'},
                        {'base_url': 'file:///tmp/key'}, {'connection': 'current'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.file_config(**changes)
        settings = ImageGenerationSettings(self.file_config())
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, '文件权限'):
            settings.initialize(self.native)
        self.assertFalse(settings.skill.exists())

    def test_conflict_readonly_and_credential_destination(self):
        stale = self.body()
        self.settings.save(self.body(model='gpt-image-1.5'), self.native)
        with self.assertRaisesRegex(ValueError, '其他页面'):
            self.settings.save(stale, self.native)
        self.settings.editable = False
        with self.assertRaisesRegex(ValueError, '只读'):
            self.settings.save(self.body(), self.native)
        custom = {'model_provider': 'other', 'model_providers': {'other': {'base_url': 'https://other.example/v1'}}}
        self.assertIsNone(self.settings.current_connection(custom)['keyFile'])
        invalid_native = {**self.native, 'openai_base_url': 'not-a-url'}
        self.assertEqual(self.settings.public(invalid_native)['currentBaseUrl'], '')
        self.settings.editable = True
        with self.assertRaisesRegex(ValueError, 'API 地址'):
            self.settings.save(self.body(enabled=True), invalid_native)
        for address in ('https://user:secret@example.test/v1', 'file:///etc/passwd', 'https://example.test/v1?key=x'):
            with self.assertRaises(ValueError):
                api_base(address)

    def test_http_gateway_without_model_list_checks_image_endpoint(self):
        body = self.body(connection='custom', baseUrl='http://images.example/v1/', apiKey='custom-secret', enabled=True)
        with patch('codex_agent.image_generation.request_json', side_effect=[(404, {}), (400, {})]) as request:
            result = self.settings.check(body, self.native)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['models'], [])
        self.assertIn('未提供模型列表', result['message'])
        self.assertEqual(request.call_args.args[:3], ('http://images.example/v1', 'custom-secret', '/images/generations'))
        saved = self.settings.save(body, self.native)
        self.assertEqual(saved['baseUrl'], 'http://images.example/v1')
        with patch('codex_agent.image_generation.request_json', side_effect=[(404, {}), (403, {})]):
            result = self.settings.check(self.body(), self.native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['httpStatus'], 403)

    def test_gateway_permission_is_not_confused_with_model_listing(self):
        with patch('codex_agent.image_generation.request_json', side_effect=[
            (200, {'data': [{'id': 'gpt-image-2'}, {'id': 'chat-model'}]}),
            (403, {'error': {'message': 'Image generation is not enabled for this group'}}),
        ]) as request:
            result = self.settings.check(self.body(), self.native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['models'], ['gpt-image-2'])
        self.assertIn('分组未开启', result['message'])
        self.assertEqual(request.call_args.args[-1]['prompt'], '')
        self.assertFalse(self.settings.path.exists())
        with patch('codex_agent.image_generation.request_json', side_effect=[(200, {'data': []}), (400, {})]):
            self.assertEqual(self.settings.check(self.body(), self.native)['status'], 'unverified')


class ImageSettingsHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_reload_idempotency_origin_and_secret_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = EventLog()
            host = FakeHost(events, root)
            host.workspace_dir = root
            host.client = SimpleNamespace(read_config=AsyncMock(return_value={'config': {}}),
                                          peer=SimpleNamespace(request=AsyncMock(return_value={})))
            settings = ImageGenerationSettings(SimpleNamespace(state_dir=root / 'state', codex_home=root / 'home', raw={}), editable=True)
            app = ConsoleApp(host, events, image_settings=settings)
            public = payload(await call(app, 'GET', '/api/image-generation/settings'))
            body = {**DEFAULTS, 'connection': 'custom', 'baseUrl': 'https://images.example/v1',
                    'apiKey': 'http-test-secret', 'revision': public['revision']}
            for _ in range(2):
                response = await call(app, 'POST', '/api/image-generation/settings', body, {'idempotency-key': 'image-save'})
                self.assertEqual(response[0]['status'], 200, payload(response))
                self.assertNotIn('http-test-secret', json.dumps(payload(response)))
            host.client.peer.request.assert_awaited_once()
            self.assertNotIn('http-test-secret', host.store.read('http-requests/image-save.json').__repr__())
            self.assertEqual((await call(app, 'POST', '/api/image-generation/check', body,
                                        {'origin': 'https://evil.example'}))[0]['status'], 403)
            self.assertEqual((await call(app, 'GET', '/api/image-generation/check'))[0]['status'], 405)


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class ImageSettingsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_only_startup_discovers_skill_without_web_save(self):
        from codex_agent.app import build_host
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE'])
            (root / 'home').mkdir()
            (root / 'home/config.toml').write_text('model = "fixture"\n')
            private_write(root / 'image-key', 'fixture-secret')
            config = AgentConfig.from_dict({
                'schema_version': 'codex-agent/v2',
                'paths': {'codex_home': 'home', 'runtime_dir': str(package), 'state_dir': 'state', 'workspace_dir': 'workspace'},
                'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.163.0'},
                'image_generation': {'enabled': True, 'connection': 'custom', 'base_url': 'http://localhost/v1',
                                     'api_key_file': 'image-key'},
            }, root)
            settings = ImageGenerationSettings(config)
            host, app = build_host(config, development=True)
            try:
                await host.start()
                self.assertTrue(host.ready)
                result = await host.client.peer.request('skills/list', {'cwds': [str(config.workspace_dir)], 'forceReload': True})
                skills = [skill for entry in result['data'] for skill in entry['skills']]
                self.assertIn('imagegen-api', [skill['name'] for skill in skills])
                self.assertTrue(app.image_settings.public({})['enabled'])
                self.assertFalse(settings.path.exists())
            finally:
                await host.stop()
                await host.events.close()


if __name__ == '__main__':
    unittest.main()
