"""Real native provider switching with isolated HOME and loopback inference."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import unittest

import test_host_core
from codex_agent.rpc import AppServerClient, JsonRpcPeer, RpcError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from runtime_probe import sse


@unittest.skipUnless(os.environ.get('CODEX_SETTINGS_TEST_BINARY'), 'native runtime opt-in required')
class ModelProviderRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def exercise(self, history_mode):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.path, body['model']))
                output = sse([{'id': 'msg', 'type': 'message', 'role': 'assistant',
                               'content': [{'type': 'output_text', 'text': 'Fixture reply.'}]}], 'response')
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(output)))
                self.end_headers()
                self.wfile.write(output)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        root = Path(self.directory.name)
        home = root / 'home'
        home.mkdir()
        config = 'model="gpt-5.4"\nmodel_provider="first"\napproval_policy="never"\nsandbox_mode="read-only"\n'
        for provider in ('first', 'second'):
            config += (f'[model_providers.{provider}]\nname="{provider}"\n'
                f'base_url="http://127.0.0.1:{server.server_port}/{provider}/v1"\n'
                'wire_api="responses"\nrequires_openai_auth=false\n')
        (home / 'config.toml').write_text(config)

        async def start():
            process = await asyncio.create_subprocess_exec(os.environ['CODEX_SETTINGS_TEST_BINARY'],
                'app-server', '--listen', 'stdio://', '--strict-config', cwd=root,
                env={'PATH': os.environ['PATH'], 'HOME': str(home), 'CODEX_HOME': str(home)},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
            peer = JsonRpcPeer(process.stdout, process.stdin, notification=self.host.handle_notification)
            self.host.client = AppServerClient(peer)
            await self.host.client.initialize()
            return process, peer

        async def stop(process, peer):
            await peer.close()
            if process.returncode is None:
                process.terminate()
            await process.wait()

        async def send(key):
            await self.host.send_message('native', key, 'Reply with fixture text.')
            async with asyncio.timeout(20):
                while self.host.get_session('native')['status'] != 'ready':
                    await asyncio.sleep(.02)
            thread = (await self.host.client.read_thread(thread_id))['thread']
            self.assertEqual(thread['turns'][-1]['status'], 'completed', thread['turns'][-1])

        process, peer = await start()
        try:
            session = await self.host.create_session('native', options={'historyMode': history_mode, 'cwd': str(root)})
            thread_id = session['thread_id']
            await self.host.native.call('thread/settings/update', {'threadId': thread_id, 'effort': 'high',
                'summary': 'none', 'sandboxPolicy': {'type': 'readOnly'}}, 'settings')
            switched = await self.host.resume('native', options={'modelProvider': 'second', 'model': 'gpt-5.4'})
            self.assertEqual(switched['settings']['modelProvider'], 'second')
            self.assertEqual(switched['settings']['effort'], 'high')
            await send('second')
            self.assertEqual(received[-1], ('/second/v1/responses', 'gpt-5.4'))
            with self.assertRaises(RpcError):
                await self.host.resume('native', options={'modelProvider': 'missing'})
            self.assertEqual(self.host.get_session('native')['status'], 'ready')
            await send('after-rejection')
            self.assertEqual(received[-1][0], '/second/v1/responses')
            await stop(process, peer)
            self.host._attached.clear()
            process, peer = await start()
            await self.host.resume('native')
            await send('after-restart')
            self.assertEqual(received[-1][0], '/second/v1/responses')
            default = await self.host.resume('native', options={'modelProvider': None})
            self.assertEqual(default['settings']['modelProvider'], 'first')
            await send('default')
            self.assertEqual(received[-1][0], '/first/v1/responses')
            self.assertEqual(len((await self.host.client.read_thread(thread_id))['thread']['turns']), 4)
        finally:
            await stop(process, peer)
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            worker.join(timeout=5)

    async def test_legacy_provider_routing_and_restart(self):
        await self.exercise('legacy')

    async def test_paginated_provider_routing_and_restart(self):
        await self.exercise('paginated')
