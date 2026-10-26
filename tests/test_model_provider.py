"""Provider reloads preserve session identity and fail closed on uncertainty."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import test_host_core
from codex_agent.host import HostNotReady
from codex_agent.rpc import RpcError


class ModelProviderTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def prepare(self):
        session = await self.host.create_session('provider')
        session['settings'].update(model='old-model', modelProvider='first', effort='high',
            approvalPolicy='never', sandboxPolicy={'type': 'readOnly'},
            collaborationMode={'mode': 'plan', 'settings': {
                'model': 'old-model', 'reasoning_effort': 'high', 'developer_instructions': None}})
        self.host._save(session)
        self.client.peer = SimpleNamespace(healthy=True, request=AsyncMock(return_value={}))
        self.client.resume = AsyncMock(return_value={'thread': {'id': session['thread_id']},
            'modelProvider': 'second', 'model': 'new-model', 'reasoningEffort': 'medium'})
        return session

    async def test_reload_preserves_identity_history_permissions_and_plan(self):
        session = await self.prepare()
        result = await self.host.native.call('thread/resume', {
            'threadId': session['thread_id'], 'modelProvider': 'second', 'model': 'new-model'}, 'switch')
        current = result['session']
        self.assertEqual(current['thread_id'], session['thread_id'])
        self.assertEqual(current['settings']['modelProvider'], 'second')
        self.assertEqual(current['settings']['model'], 'new-model')
        self.assertEqual(current['settings']['effort'], 'high')
        self.assertEqual(current['settings']['sandboxPolicy']['type'], 'readOnly')
        self.assertEqual(current['settings']['collaborationMode']['settings']['model'], 'new-model')
        self.assertEqual(self.client.peer.request.call_args_list[0].args[0], 'thread/unsubscribe')
        self.assertEqual(self.store.read('sessions/provider.json')['settings'], current['settings'])
        self.host._attached.clear()
        await self.host.resume('provider')
        self.assertEqual(self.client.resume.call_args.kwargs['options']['modelProvider'], 'second')

    async def test_default_provider_is_resolved_before_resume(self):
        await self.prepare()
        self.client.peer.request.return_value = {'config': {'model_provider': 'second'}}
        result = await self.host.resume('provider', options={'modelProvider': None})
        self.assertEqual(self.client.resume.call_args.kwargs['options']['modelProvider'], 'second')
        self.assertEqual(result['settings']['modelProvider'], 'second')

    async def test_running_and_starting_sessions_never_detach(self):
        session = await self.prepare()
        for status in ('starting', 'running', 'interrupting', 'closing'):
            session['status'] = status
            self.host._save(session)
            with self.assertRaises(HostNotReady):
                await self.host.resume('provider', options={'modelProvider': 'second'})
        self.client.peer.request.assert_not_awaited()

    async def test_definite_rejection_restores_previous_settings(self):
        await self.prepare()
        self.client.resume.side_effect = [RpcError({'message': 'unknown provider'}), {}]
        with self.assertRaises(RpcError):
            await self.host.resume('provider', options={'modelProvider': 'missing'})
        current = self.host.get_session('provider')
        self.assertEqual(current['status'], 'ready')
        self.assertEqual(current['settings']['modelProvider'], 'first')
        self.assertEqual(self.client.resume.call_args.kwargs['options']['modelProvider'], 'first')
        self.assertIn('provider', self.host._attached)

    async def test_timeout_does_not_attempt_an_unsafe_rollback(self):
        await self.prepare()
        self.client.resume.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.host.resume('provider', options={'modelProvider': 'second'})
        self.assertEqual(self.client.resume.await_count, 1)
        self.assertEqual(self.host.get_session('provider')['status'], 'recovery_required')
        self.assertNotIn('provider', self.host._attached)

    async def test_empty_thread_is_materialized_without_inventing_a_turn(self):
        session = await self.prepare()
        self.client.read_thread = AsyncMock(side_effect=[RpcError({'message': 'thread is not materialized yet'}),
            {'thread': {'id': session['thread_id'], 'turns': []}}])
        self.client.peer.request.return_value = {'thread': {'status': {'type': 'idle'}}}
        await self.host.resume('provider', options={'modelProvider': 'second'})
        methods = [call.args[0] for call in self.client.peer.request.call_args_list]
        self.assertLess(methods.index('thread/name/set'), methods.index('thread/unsubscribe'))
        self.assertEqual(self.client.turns, [])

    async def test_conda_reload_does_not_restore_the_old_model(self):
        session = await self.prepare()
        session['conda_environment'] = {'name': 'science', 'prefix': '/env/science'}
        self.host._save(session)
        variables = {'PATH': '/env/science/bin:/usr/bin'}
        with patch('codex_agent.host.activate_environment', return_value=(session['conda_environment'], variables)):
            current = await self.host.resume('provider', options={'modelProvider': 'second', 'model': 'new-model'})
        self.assertEqual(current['settings']['model'], 'new-model')
        self.assertEqual(self.host.command_environment('provider'), variables)
        params = self.client.peer.request.call_args.args[1]
        self.assertEqual(params['model'], 'new-model')

    async def test_unconfirmed_provider_cannot_be_reported_as_successful(self):
        await self.prepare()
        self.client.resume.return_value = {'thread': {'id': 'thread-1'}, 'modelProvider': 'first'}
        with self.assertRaises(HostNotReady):
            await self.host.resume('provider', options={'modelProvider': 'second'})
        self.assertNotIn('provider', self.host._attached)
        self.assertEqual(self.host.get_session('provider')['settings']['modelProvider'], 'first')
