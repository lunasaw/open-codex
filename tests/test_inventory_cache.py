import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from codex_agent.native import NativeBridge


class InventoryCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesces_tabs_isolates_threads_and_does_not_cache_tools(self):
        future = asyncio.get_running_loop().create_future()
        async def request(*args, **kwargs):
            return await future
        peer = SimpleNamespace(request=AsyncMock(side_effect=request))
        bridge = NativeBridge(SimpleNamespace(client=SimpleNamespace(peer=peer)))
        a = asyncio.create_task(bridge._request('mcpServerStatus/list', {'threadId': 'a'}, timeout=60))
        b = asyncio.create_task(bridge._request('mcpServerStatus/list', {'threadId': 'a'}, timeout=60))
        await asyncio.sleep(.01)
        self.assertEqual(peer.request.await_count, 1)
        future.set_result({'data': [{'name': 'tools'}]})
        first, second = await asyncio.gather(a, b)
        first['data'].clear()
        self.assertTrue(second['data'])
        self.assertTrue((await bridge._request('mcpServerStatus/list', {'threadId': 'a'}, timeout=60))['data'])
        self.assertEqual(peer.request.await_count, 1)
        await bridge._request('mcpServerStatus/list', {'threadId': 'b'}, timeout=60)
        self.assertEqual(peer.request.await_count, 2)
        for _ in range(2):
            await bridge._request('mcpServer/tool/call', {}, timeout=60)
        self.assertEqual(peer.request.await_count, 4)
        bridge.invalidate_mcp_inventory()
        await bridge._request('mcpServerStatus/list', {'threadId': 'a'}, timeout=60)
        self.assertEqual(peer.request.await_count, 5)

    async def test_cancellation_does_not_cancel_shared_read_and_failures_are_retryable(self):
        future = asyncio.get_running_loop().create_future()
        async def request(*args, **kwargs): return await future
        peer = SimpleNamespace(request=AsyncMock(side_effect=request))
        bridge = NativeBridge(SimpleNamespace(client=SimpleNamespace(peer=peer)))
        task = asyncio.create_task(bridge._request('mcpServerStatus/list', {}, timeout=60))
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        future.set_result({'data': []})
        self.assertEqual(await bridge._request('mcpServerStatus/list', {}, timeout=60), {'data': []})
        self.assertEqual(peer.request.await_count, 1)
        bridge.invalidate_mcp_inventory()
        peer.request.side_effect = RuntimeError('offline')
        with self.assertRaisesRegex(RuntimeError, 'offline'):
            await bridge._request('mcpServerStatus/list', {}, timeout=60)
        peer.request.side_effect = None
        peer.request.return_value = {'data': []}
        self.assertEqual(await bridge._request('mcpServerStatus/list', {}, timeout=60), {'data': []})
