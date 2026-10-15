"""Bounded batching for expendable transfer telemetry, separate from signed state.

At most the unflushed interval of counters/events can be lost on a process crash.
Consensus transactions, lease fencing, outbox and receipts retain their existing
durable paths. This adapter never writes those tables.
"""
from collections import Counter, deque
from contextlib import closing
import time
from dawl_node.common import canonical_json


class TransferTelemetry:
    def __init__(self, client, interval=1.0, batch=128):
        self.client = client; self.interval = interval; self.batch = batch
        self.counters = Counter(); self.events = deque(maxlen=256)
        self.updates = 0; self.last = time.monotonic()

    def increment(self, name, amount=1):
        self.counters[name] += amount; self.tick()

    def event(self, kind, **fields):
        self.events.append(canonical_json({'time': time.time(), 'kind': kind, **fields})); self.tick()

    def tick(self):
        self.updates += 1
        if self.updates >= self.batch or time.monotonic()-self.last >= self.interval: self.flush()

    def flush(self):
        if not self.updates: return
        with closing(self.client.connect()) as db, db:
            db.executemany('INSERT INTO counters VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value',
                           list(self.counters.items()))
            db.executemany('INSERT INTO events(payload) VALUES (?)', ((event,) for event in self.events))
            db.execute('DELETE FROM events WHERE id < (SELECT MAX(id)-256 FROM events)')
        self.counters.clear(); self.events.clear(); self.updates = 0; self.last = time.monotonic()
