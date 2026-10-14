"""Batching preserves metrics while leaving the durable signed outbox alone."""
from pathlib import Path
import sys
import sqlite3
import tempfile
import unittest
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_cluster.telemetry import TransferTelemetry


class TelemetryTests(unittest.TestCase):
    def test_batch_counters_events_and_signed_outbox_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'worker.sqlite'
            class Client:
                def connect(self): return sqlite3.connect(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript('CREATE TABLE counters(name TEXT PRIMARY KEY,value INTEGER); CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,payload BLOB); CREATE TABLE outbox(payload BLOB); INSERT INTO outbox VALUES (X\'010203\');')
            metrics=TransferTelemetry(Client(),interval=60,batch=128)
            for _ in range(100): metrics.increment('received_bytes',3)
            with closing(sqlite3.connect(path)) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM counters').fetchone()[0],0)
            for i in range(400): metrics.event('artifact_complete',index=i)
            metrics.flush(); metrics.flush()
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute('SELECT value FROM counters').fetchone()[0],300)
                self.assertEqual(db.execute('SELECT payload FROM outbox').fetchone()[0],b'\x01\x02\x03')
                self.assertLessEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],257)
                self.assertIn(b'399',db.execute('SELECT payload FROM events ORDER BY id DESC LIMIT 1').fetchone()[0])
            self.assertEqual(metrics.updates,0)


if __name__=='__main__': unittest.main()
