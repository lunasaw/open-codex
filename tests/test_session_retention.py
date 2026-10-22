"""Lifecycle cleanup removes content durably without disturbing other chats."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_host_core
from codex_agent.console.adapters.sqlite import SqliteConsoleObservationStore
from codex_agent.console.models import EventQuery, ObservationBatch, SnapshotQuery
from codex_agent.console.ports import PageContextMismatch, UnknownEvent
from codex_agent.console.service import ConsoleObservationService
from codex_agent.rpc import RpcError


class SessionRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_reclaims_disk_preserves_other_chat_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.sqlite3'
            service = ConsoleObservationService(SqliteConsoleObservationStore(path), epoch='e')
            await service.start()
            self.addAsyncCleanup(service.close)
            service.bind('removed', 'thread-removed')
            service.bind('kept', 'thread-kept')
            for i in range(4):
                for session in ('removed', 'kept'):
                    service.append(session, {'kind': 'message_accepted', 'host_request_id': f'{session}-{i}',
                        'public': {'text': ('removed-secret-' + 'x' * 300000) if session == 'removed' else f'kept-{i}'}})
            # An event observed before a binding still belongs to the native thread.
            service.observer('runtime_to_host', {'method': 'item/agentMessage/delta', 'params': {
                'threadId': 'thread-removed', 'turnId': 'u', 'itemId': 'i', 'delta': 'removed-secret-late'}})
            service.observer('runtime_to_host', {'id': 900, 'result': {'data': [
                {'id': 'thread-removed', 'preview': 'removed-secret-preview'}, {'id': 'thread-kept', 'preview': 'keep-preview'}]}})
            await service.flush()
            original = await service.read_events(EventQuery(limit=1000))
            before = await service.snapshot(SnapshotQuery('kept', limit=1))
            removed_id = next(row['event_id'] for row in original['events'] if row.get('session_id') == 'removed')
            bytes_before = path.stat().st_size + Path(str(path) + '-wal').stat().st_size
            waiter = asyncio.create_task(service.wait_for_change(original['watermark'], 5))
            await asyncio.sleep(0)
            service.retire_sessions({'thread-removed': 'removed'})
            await service.flush()
            self.assertEqual(await asyncio.wait_for(waiter, 1), original['watermark'])
            self.assertEqual((await service.read_events(EventQuery(session_id='removed')))['events'], [])
            self.assertEqual((await service.snapshot(SnapshotQuery('removed')))['messages'], [])
            self.assertEqual([m['text'] for m in (await service.snapshot(SnapshotQuery('kept')))['messages']], [f'kept-{i}' for i in range(4)])
            kept = await service.read_events(EventQuery(session_id='kept', limit=1000))
            self.assertEqual([row['event_id'] for row in kept['events']], [row['event_id'] for row in original['events'] if row.get('session_id') == 'kept'])
            with self.assertRaises(UnknownEvent):
                await service.detail(removed_id)
            with self.assertRaises(PageContextMismatch):
                await service.snapshot(SnapshotQuery('kept', limit=1, page=before['next_page']))
            self.assertLess(path.stat().st_size, bytes_before // 2)
            self.assertNotIn(b'removed-secret', path.read_bytes())
            self.assertNotIn('removed-secret', json.dumps((await service.read_events(EventQuery(limit=1000)))['events']))
            # Late output and later global listings cannot resurrect the content.
            service.append('removed', {'kind': 'message_accepted', 'host_request_id': 'late', 'public': {'text': 'removed-secret'}})
            service.observer('runtime_to_host', {'method': 'item/agentMessage/delta', 'params': {'threadId': 'thread-removed', 'delta': 'removed-secret'}})
            await service.flush()
            self.assertEqual((await service.read_events(EventQuery(session_id='removed')))['events'], [])
            watermark = (await service.read_events(EventQuery()))['watermark']
            await service.close()
            service = ConsoleObservationService(SqliteConsoleObservationStore(path))
            await service.start()
            self.addAsyncCleanup(service.close)
            self.assertEqual((await service.read_events(EventQuery()))['watermark'], watermark)
            service.append('removed', {'kind': 'message_accepted', 'public': {'text': 'removed-secret'}})
            service.append('kept', {'kind': 'message_accepted', 'host_request_id': 'new', 'public': {'text': 'after restart'}})
            await service.flush()
            new = await service.read_events(EventQuery(after=watermark))
            self.assertEqual(len(new['events']), 1)
            self.assertEqual(new['events'][0]['session_id'], 'kept')
            # Unarchive enables a fresh projection from native history.
            service.allow_session('thread-removed')
            service.bind('removed', 'thread-removed')
            service.append('removed', {'kind': 'thread_restored', 'thread_id': 'thread-removed', 'public': {'thread': {
                'turns': [{'id': 'u', 'status': 'completed', 'items': [{'id': 'i', 'type': 'agentMessage', 'text': 'from native JSONL'}]}]}}})
            await service.flush()
            self.assertEqual((await service.snapshot(SnapshotQuery('removed')))['messages'][0]['text'], 'from native JSONL')

    async def test_mixed_batch_and_empty_database_keep_monotonic_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.db'
            store = SqliteConsoleObservationStore(path)
            await store.open('e')
            rows = tuple({'epoch': 'e', 'seq': str(i), 'event_id': f'e:{i}', 'session_id': session,
                          'payload': {'text': session}} for i, session in enumerate(('gone', 'keep', 'gone'), 1))
            await store.commit(ObservationBatch('mixed', 'e', rows))
            await store.retire_sessions({'t': 'gone'})
            self.assertEqual([r['seq'] for r in (await store.read_events(EventQuery()))['events']], ['2'])
            await store.retire_sessions({'other': 'keep'})
            await store.close()
            store = SqliteConsoleObservationStore(path)
            await store.open('ignored')
            self.addAsyncCleanup(store.close)
            self.assertEqual((await store.stats())['watermark'], 'e:3')
            self.assertEqual((await store.stats())['event_count'], 0)
            await store.commit(ObservationBatch('next', 'e', ({**rows[0], 'seq': '4', 'event_id': 'e:4', 'session_id': 'new'},)))
            self.assertEqual((await store.stats())['watermark'], 'e:4')

    async def test_terminal_output_is_correlated_and_removed_with_session(self):
        service = ConsoleObservationService()
        await service.start()
        self.addAsyncCleanup(service.close)
        service.observer('host_to_runtime', {'id': 1, 'method': 'command/exec', 'params': {'processId': 'p'}},
                         context={'session_id': 's'})
        service.observer('runtime_to_host', {'method': 'command/exec/outputDelta', 'params': {'processId': 'p', 'deltaBase64': 'eA=='}})
        await service.flush()
        self.assertEqual(len((await service.read_events(EventQuery(session_id='s')))['events']), 2)
        service.retire_sessions({'t': 's'})
        service.observer('runtime_to_host', {'method': 'command/exec/outputDelta', 'params': {'processId': 'p', 'deltaBase64': 'eA=='}})
        await service.flush()
        self.assertEqual((await service.read_events(EventQuery()))['events'], [])


class HostRetentionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_host_core.HostTests.asyncSetUp
    asyncTearDown = test_host_core.HostTests.asyncTearDown

    async def test_native_failure_keeps_events_success_purges_and_unarchive_restores(self):
        session = await self.host.create_session('s')
        tid = session['thread_id']
        self.events.append('s', {'kind': 'message_accepted', 'host_request_id': 'r', 'public': {'text': 'native-history'}})
        await self.events.flush()
        self.client.peer = SimpleNamespace(healthy=True, request=AsyncMock(side_effect=RpcError({'message': 'rejected'})))
        for method in ('thread/archive', 'thread/delete'):
            with self.assertRaises(RpcError):
                await self.host.native.call(method, {'threadId': tid}, method.replace('/', '-'))
            self.assertTrue((await self.events.snapshot(SnapshotQuery('s')))['messages'])
        self.client.peer.request.side_effect = None
        self.client.peer.request.return_value = {}
        await self.host.native.call('thread/archive', {'threadId': tid}, 'archive-success')
        self.assertTrue(self.host.get_session('s')['archived'])
        self.assertEqual((await self.events.read_events(EventQuery(session_id='s')))['events'], [])
        self.client.resume = AsyncMock(return_value={'thread': {'id': tid, 'turns': [{'id': 'u', 'status': 'completed', 'items': [
            {'id': 'i', 'type': 'agentMessage', 'text': 'native-history'}]}]}})
        await self.host.native.call('thread/unarchive', {'threadId': tid}, 'unarchive')
        await self.events.flush()
        self.assertEqual((await self.events.snapshot(SnapshotQuery('s')))['messages'][0]['text'], 'native-history')
        await self.host.native.call('thread/delete', {'threadId': tid}, 'delete-success')
        self.assertIsNone(self.host.get_session('s'))
        self.assertEqual((await self.events.read_events(EventQuery(session_id='s')))['events'], [])
        await self.host.handle_notification({'method': 'thread/deleted', 'params': {'threadId': tid}})
        await self.events.flush()
        self.assertEqual((await self.events.read_events(EventQuery(session_id='s')))['events'], [])

    async def test_native_notification_cleans_threads_without_host_session(self):
        self.events.observer('runtime_to_host', {'method': 'item/agentMessage/delta', 'params': {
            'threadId': 'external', 'turnId': 'u', 'itemId': 'i', 'delta': 'external-history'}})
        await self.events.flush()
        await self.host.handle_notification({'method': 'thread/archived', 'params': {'threadId': 'external'}})
        await self.events.flush()
        self.assertEqual((await self.events.read_events(EventQuery(filters={'thread_id': 'external'})))['events'], [])
