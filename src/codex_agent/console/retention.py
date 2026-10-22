"""Remove one chat's observations without renumbering other chats' cursors."""
from .models import ObservationBatch


class RetiredScopes:
    def __init__(self, scopes):
        self.threads = {thread for thread, session in scopes.items() if thread}
        self.sessions = {session for session in scopes.values() if session}

    def owns(self, row):
        return (row.get('thread_id') in self.threads or row.get('session_id') in self.sessions)

    def payload(self, value):
        if not self.threads:
            return value
        # Global thread/list responses can contain several chats. Remove only
        # the retired thread objects, preserving unrelated entries and cursors.
        if isinstance(value, dict):
            if any(value.get(key) in self.threads for key in ('id', 'threadId', 'thread_id')
                   if isinstance(value.get(key), str)):
                return None
            return {key: self.payload(child) for key, child in value.items()}
        if isinstance(value, list):
            return [clean for child in value if (clean := self.payload(child)) is not None]
        return value

    def event(self, row):
        if self.owns(row):
            return None
        payload = self.payload(row.get('payload'))
        if payload is None:
            return None
        return row if payload == row.get('payload') else {**row, 'payload': payload}

    def batch(self, batch):
        events = tuple(clean for row in batch.events if (clean := self.event(row)) is not None)
        seqs = {row['seq'] for row in events}
        changes = tuple(row for row in batch.changes if row['seq'] in seqs and not self.owns(row))
        return ObservationBatch(batch.batch_id, batch.epoch, events, changes)
