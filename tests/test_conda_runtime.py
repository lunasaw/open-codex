"""Exercise real native thread reload and shell policy with an isolated HOME."""
import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_host_core
from codex_agent.rpc import AppServerClient, JsonRpcPeer


@unittest.skipUnless(os.environ.get('CODEX_SETTINGS_TEST_BINARY'), 'native runtime opt-in required')
class CondaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def test_binding_restart_switch_and_unbind_change_native_shell_python(self):
        root = Path(self.directory.name)
        home = root / 'native-home'
        home.mkdir()
        (home / 'config.toml').write_text('model="gpt-5.4"\nmodel_provider="fixture"\n'
            'approval_policy="never"\nsandbox_mode="danger-full-access"\n'
            '[shell_environment_policy.set]\nBASE_MARK="preserved"\n'
            '[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:9/v1"\n'
            'wire_api="responses"\nrequires_openai_auth=false\n')
        messages = []
        async def receive(message):
            messages.append(message)
            await self.host.handle_notification(message)
        async def start_runtime():
            process = await asyncio.create_subprocess_exec(
                os.environ['CODEX_SETTINGS_TEST_BINARY'], 'app-server', '--listen', 'stdio://', '--strict-config',
                cwd=root, env={'PATH': os.environ['PATH'], 'HOME': str(home), 'CODEX_HOME': str(home)},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024)
            peer = JsonRpcPeer(process.stdout, process.stdin, notification=receive)
            self.host.client = AppServerClient(peer)
            await self.host.client.initialize()
            return process, peer
        async def stop_runtime(process, peer):
            await peer.close()
            if process.returncode is None:
                process.terminate()
            await process.wait()
        process, peer = await start_runtime()
        async def shell(command):
            baseline = len(messages)
            await self.host.native.call('thread/shellCommand', {'threadId': thread, 'command': command, 'timeoutMs': 3000}, 'shell-' + str(baseline))
            async with asyncio.timeout(15):
                while self.host.get_session('native')['status'] != 'ready':
                    await asyncio.sleep(.02)
            items = [n['params']['item'] for n in messages[baseline:] if n['method'] == 'item/completed'
                     and n['params'].get('threadId') == thread and n['params']['item']['type'] == 'commandExecution']
            self.assertTrue(items)
            self.assertEqual(items[-1]['exitCode'], 0, items[-1])
            return items[-1]['aggregatedOutput']
        try:
            session = await self.host.create_session('native', options={'historyMode': 'legacy', 'cwd': str(root)})
            thread = session['thread_id']
            self.assertIn('preserved', await shell('printf "$BASE_MARK"'))
            default_python = await shell('command -v python')
            for name in ('science', 'another'):
                prefix = root / name
                (prefix / 'bin').mkdir(parents=True)
                (prefix / 'bin/python').write_text('#!/bin/sh\nprintf "' + name + '-python"\n')
                (prefix / 'bin/python').chmod(0o755)
                environment = {'name': name, 'prefix': str(prefix)}
                variables = {'PATH': str(prefix / 'bin') + ':/usr/bin:/bin', 'CONDA_PREFIX': str(prefix)}
                with patch('codex_agent.host.activate_environment', return_value=(environment, variables)):
                    await self.host.set_conda_environment('native', str(prefix))
                output = await shell('python; printf "|$CONDA_PREFIX|$BASE_MARK"')
                self.assertIn(name + '-python|' + str(prefix) + '|preserved', output)
                if name == 'science':
                    await stop_runtime(process, peer)
                    self.host._attached.clear()
                    self.host._conda_environments.clear()
                    self.host._sessions.clear()
                    process, peer = await start_runtime()
                    with patch('codex_agent.host.activate_environment', return_value=(environment, variables)):
                        await self.host.resume('native')
                    self.assertIn('science-python|' + str(prefix), await shell('python; printf "|$CONDA_PREFIX"'))
            with patch('codex_agent.host.activate_environment', return_value=(None, {})):
                await self.host.set_conda_environment('native', None)
            output = await shell('printf "$CONDA_PREFIX|$BASE_MARK"')
            self.assertEqual(output, '|preserved')
            self.assertEqual(await shell('command -v python'), default_python)
            self.assertEqual(self.host.get_session('native')['thread_id'], thread)
        finally:
            await stop_runtime(process, peer)
