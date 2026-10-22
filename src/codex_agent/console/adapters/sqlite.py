"""Durable observations; archived/deleted chat content is physically removed."""
import asyncio
import hashlib
import json
import os
import sqlite3

from .memory import MemoryConsoleObservationStore, encoded
from ..models import ObservationBatch
from ..ports import CommitConflict
from ..projection import Projector
from ..retention import RetiredScopes


class SqliteConsoleObservationStore(MemoryConsoleObservationStore):
    def __init__(self, path):
        super().__init__()
        self.path, self.db = path, None
        self.maintenance_error = None

    async def open(self, epoch):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        # The single observation writer serializes DB access. Large cleanup and
        # compaction run on a worker so RPC/SSE and other chats remain responsive.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS batches (position INTEGER PRIMARY KEY, id TEXT UNIQUE, payload BLOB NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS retired_scopes (thread_id TEXT PRIMARY KEY, session_id TEXT)')
        self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('epoch', epoch))
        self.db.commit()
        metadata = dict(self.db.execute('SELECT key,value FROM metadata'))
        await super().open(metadata['epoch'])
        self.retired_scopes = dict(self.db.execute('SELECT thread_id,session_id FROM retired_scopes'))
        self.history_revision = int(metadata.get('history_revision', 0))
        await asyncio.to_thread(self._restore)
        self._watermark = max(self._watermark, int(metadata.get('watermark', 0)))
        return await self.stats()

    def _restore(self):
        self.projector = Projector()
        for (raw,) in self.db.execute('SELECT payload FROM batches ORDER BY position'):
            data = json.loads(raw)
            events, changes = data['events'], data['changes']
            # Retention leaves gaps. Sequence identities are never reused, so
            # live browser cursors and unrelated chats remain valid on restart.
            for row in events:
                seq = int(row['seq'])
                if seq <= self._watermark or row['event_id'] != self._cursor(seq):
                    raise CommitConflict('invalid durable event sequence')
                self._events[seq] = row
                self._watermark = seq
                self._bytes += len(encoded(row))
            self._bindings.extend(c for c in changes if c.get('type') == 'binding')
            if all(row.get('schema_version') in {'galatea.console.event/v1', 'codex-agent.console.event/v1'} for row in events):
                for row in events:
                    self._changes.extend(self.projector.changes(row))
                self._changes.extend(c for c in changes if c.get('type') == 'binding')
            else:
                self._changes.extend(changes)
            fingerprint = hashlib.sha256(encoded({'events': events, 'changes': changes})).hexdigest()
            self._batches[data['batch_id']] = (fingerprint, {'watermark': self._cursor(self._watermark), 'event_count': len(events)})

    async def _before_publish(self, batch):
        raw = encoded({'batch_id': batch.batch_id, 'events': batch.events, 'changes': batch.changes})
        previous = self.db.execute('SELECT payload FROM batches WHERE id=?', (batch.batch_id,)).fetchone()
        if previous and previous[0] != raw:
            raise CommitConflict('durable batch identity conflict')
        if not previous:
            with self.db:
                self.db.execute('INSERT INTO batches(id,payload) VALUES (?,?)', (batch.batch_id, raw))
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('watermark', batch.events[-1]['seq']))

    def _retire(self, scopes):
        policy = RetiredScopes(scopes)
        events, changes = self._retention_plan(scopes)
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO retired_scopes VALUES (?,?)', scopes.items())
            self.db.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)', [
                ('watermark', str(self._watermark)), ('history_revision', str(self.history_revision + 1))])
            # A batch can contain bindings or records for several chats. Never
            # delete a whole mixed batch just because one event belongs to it.
            for position, raw in self.db.execute('SELECT position,payload FROM batches ORDER BY position'):
                data = json.loads(raw)
                old = ObservationBatch(data['batch_id'], self.epoch, tuple(data['events']), tuple(data['changes']))
                new = policy.batch(old)
                if new == old:
                    continue
                if not new.events:
                    self.db.execute('DELETE FROM batches WHERE position=?', (position,))
                else:
                    self.db.execute('UPDATE batches SET payload=? WHERE position=?',
                                    (encoded({'batch_id': new.batch_id, 'events': new.events, 'changes': new.changes}), position))
        return events, changes

    def _compact(self):
        try:
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.db.execute('VACUUM')
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.maintenance_error = None
        except sqlite3.OperationalError as exc:
            # Deletion has committed. A concurrent external reader can defer
            # disk compaction; do not undo or misreport the native operation.
            self.maintenance_error = str(exc)

    async def retire_sessions(self, scopes):
        self._check()
        scopes = {**self.retired_scopes, **{k: v or self.retired_scopes.get(k) for k, v in scopes.items()}}
        if scopes == self.retired_scopes:
            return
        events, changes = await asyncio.to_thread(self._retire, scopes)
        await self._publish_retention(scopes, events, changes)
        await asyncio.to_thread(self._compact)

    async def allow_session(self, thread_id):
        with self.db:
            self.db.execute('DELETE FROM retired_scopes WHERE thread_id=?', (thread_id,))
        await super().allow_session(thread_id)

    async def stats(self):
        return {**await super().stats(), 'backend': 'sqlite', 'retention': 'persistent',
                'cleanup': 'archive_or_delete', 'maintenance_error': self.maintenance_error}

    async def close(self):
        await super().close()
        if self.db:
            self.db.close()
            self.db = None
