"""Session environment isolation, activation and terminal receipt behavior."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_host_core
from test_console import call, payload
from codex_agent.conda import _run, activate_environment, list_environments
from codex_agent.console.server import ConsoleApp
from codex_agent.host import HostNotReady


class CondaDiscoveryTests(unittest.TestCase):
    def test_activation_hook_output_does_not_corrupt_environment_response(self):
        with patch('codex_agent.conda.subprocess.run', return_value=SimpleNamespace(
                stdout='Activating custom tools\n__ENV__{"HOOK_VAR":"enabled"}\nCleanup message\n')):
            self.assertEqual(_run('/conda', 'run', marker='__ENV__'), {'HOOK_VAR': 'enabled'})

    def test_unavailable_conda_and_prefix_validation(self):
        with patch('codex_agent.conda.conda_executable', return_value=None):
            self.assertFalse(list_environments()['available'])
            with self.assertRaises(ValueError):
                activate_environment('/missing')

    def test_activation_preserves_hook_variables_and_prepends_selected_python(self):
        with patch('codex_agent.conda.conda_executable', return_value='/opt/conda/bin/conda'), \
                patch.dict(os.environ, {'PATH': '/usr/bin', 'REMOVE_ME': 'original'}, clear=True), \
                patch('codex_agent.conda._run', side_effect=[
                    {'root_prefix': '/opt/conda', 'envs': ['/opt/conda', '/env with space']},
                    {'PATH': '/usr/bin:/env with space/bin', 'CONDA_PREFIX': '/env with space', 'HOOK_VAR': 'activated'}]):
            environment, variables = activate_environment('/env with space')
        self.assertEqual(environment['prefix'], '/env with space')
        self.assertEqual(variables['PATH'], '/env with space/bin:/usr/bin')
        self.assertEqual(variables['HOOK_VAR'], 'activated')
        self.assertIsNone(variables['REMOVE_ME'])


class CondaSessionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def prepare(self):
        session = await self.host.create_session('conda')
        self.client.peer = SimpleNamespace(healthy=True, request=AsyncMock(return_value={}))
        self.client.resume = AsyncMock(return_value={'thread': {'id': session['thread_id']}})
        self.environment = {'name': 'science', 'prefix': '/conda/envs/science'}
        self.variables = {'PATH': '/conda/envs/science/bin:/usr/bin', 'CONDA_PREFIX': '/conda/envs/science'}
        return session

    async def test_binding_reload_resume_and_unbind_preserve_session_and_settings(self):
        session = await self.prepare()
        session['settings'].update(model='custom-model', effort='high', sandboxPolicy={'type': 'readOnly'},
                                   config={'shell_environment_policy.set.EXISTING': 'kept'})
        self.host._save(session)
        with patch('codex_agent.host.activate_environment', return_value=(self.environment, self.variables)):
            bound = await self.host.set_conda_environment('conda', self.environment['prefix'])
            self.assertEqual(bound['thread_id'], session['thread_id'])
            options = self.client.resume.call_args.kwargs['options']
            self.assertEqual(options['config']['shell_environment_policy.set.PATH'], self.variables['PATH'])
            self.assertEqual(options['config']['shell_environment_policy.set.EXISTING'], 'kept')
            self.assertEqual(self.host.command_environment('conda'), self.variables)
            self.assertEqual(self.store.read('sessions/conda.json')['conda_environment'], self.environment)
            self.host._attached.clear()
            self.host._conda_environments.clear()
            await self.host.resume('conda')
            self.assertEqual(self.host.command_environment('conda'), self.variables)
        with patch('codex_agent.host.activate_environment', return_value=(None, {})):
            await self.host.set_conda_environment('conda', None)
        self.assertIsNone(self.host.get_session('conda')['conda_environment'])
        self.assertNotIn('shell_environment_policy.set.PATH', self.client.resume.call_args.kwargs['options']['config'])
        self.assertEqual(self.host.command_environment('conda'), {})
        await self.host.create_session('other')
        self.assertNotIn('conda_environment', self.host.get_session('other'))

    async def test_running_or_invalid_environment_never_detaches_thread(self):
        session = await self.prepare()
        with patch('codex_agent.host.activate_environment', side_effect=ValueError('missing')):
            with self.assertRaises(ValueError):
                await self.host.set_conda_environment('conda', '/missing')
        session['active_turn_id'] = 'active'
        self.host._save(session)
        with self.assertRaises(HostNotReady):
            await self.host.set_conda_environment('conda', self.environment['prefix'])
        self.client.peer.request.assert_not_awaited()

    async def test_uncertain_switch_requires_recovery_and_reapplies_binding(self):
        await self.prepare()
        self.client.resume.side_effect = TimeoutError()
        with patch('codex_agent.host.activate_environment', return_value=(self.environment, self.variables)):
            with self.assertRaises(TimeoutError):
                await self.host.set_conda_environment('conda', self.environment['prefix'])
            self.assertEqual(self.host.get_session('conda')['status'], 'recovery_required')
            self.assertNotIn('conda', self.host._attached)
            self.client.resume.side_effect = None
            await self.host.resume('conda')
        self.assertEqual(self.host.get_session('conda')['status'], 'ready')

    async def test_session_terminal_overrides_and_receipts_stay_isolated(self):
        await self.prepare()
        with patch('codex_agent.host.activate_environment', return_value=(self.environment, self.variables)):
            await self.host.set_conda_environment('conda', self.environment['prefix'])
        app = ConsoleApp(self.host, self.events)
        for method in ('command/exec', 'process/spawn'):
            self.client.peer.request.reset_mock()
            body = {'method': method, 'session_id': 'conda', 'params': {'command': ['python'], 'env': {'USER_VAR': 'kept', 'PATH': 'wrong'}}}
            if method == 'process/spawn':
                body['params'].update(cwd='/tmp', processHandle='test-process')
            sent = await call(app, 'POST', '/api/native/call', body, {'idempotency-key': method.replace('/', '-')})
            self.assertEqual(sent[0]['status'], 200, payload(sent))
            params = self.client.peer.request.call_args.args[1]
            self.assertEqual(params['env']['PATH'], self.variables['PATH'])
            self.assertEqual(params['env']['USER_VAR'], 'kept')
            self.client.peer.request.reset_mock()
            await call(app, 'POST', '/api/native/call', body, {'idempotency-key': method.replace('/', '-')})
            self.client.peer.request.assert_not_awaited()
            self.assertNotIn('CONDA_PREFIX', str(self.store.read('native-requests/' + method.replace('/', '-') + '.json')))

    async def test_api_inventory_and_session_binding_are_same_origin(self):
        await self.prepare()
        app = ConsoleApp(self.host, self.events)
        with patch('codex_agent.console.server.list_environments', return_value={'available': True, 'environments': [self.environment]}):
            self.assertEqual(payload(await call(app, 'GET', '/api/conda/environments'))['environments'], [self.environment])
        body = {'prefix': self.environment['prefix']}
        headers = {'idempotency-key': 'bind'}
        with patch('codex_agent.host.activate_environment', return_value=(self.environment, self.variables)):
            result = await call(app, 'POST', '/api/sessions/conda/conda-environment', body, headers)
        self.assertEqual(result[0]['status'], 200)
        self.assertEqual(payload(result)['conda_environment'], self.environment)
        denied = await call(app, 'POST', '/api/sessions/conda/conda-environment', body, {**headers, 'origin': 'https://other.example'})
        self.assertEqual(denied[0]['status'], 403)
