"""Fixed-stimulus pilot. SQLite transactions preserve assignments and retry safety.

No model calls. Participant projections exclude condition labels, quality labels,
rubrics, alternative advice and other participants' records. The public showcase
does not mount this module or its database.
"""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import itertools
import json
from pathlib import Path
import secrets
import sqlite3


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def documentation(condition):
    # B and C have exactly the same statements. C adds the supporting records.
    steps = [
        {'title': 'Utgangspunkt', 'text': 'Demonstrasjonsmodellen bygger på de publiserte vektene til Qwen3-0.6B. Den nøyaktige modellversjonen er registrert.', 'file': 'base-lock.json'},
        {'title': 'Tekstgrunnlag', 'text': '16 bøker inngår i den lokale tilpasningen. Originaltekstene og inndelingen i trenings- og testdata er registrert. En separat kontroll gjenskapte alle 4 427 tekstvinduer.', 'file': 'verification.json'},
        {'title': 'Trening', 'text': 'Modellen ble finjustert lokalt i tre epoker, med 684 oppdateringer. Oppskrift, innstillinger og lagrede modellversjoner følger historikken.', 'file': 'training-summary.json'},
        {'title': 'Kontroll', 'text': 'Filkontroll og sammenslåing av modellvektene bestod. En gjentakelse av første treningsepoke ga små tallforskjeller og bestod ikke kravet om helt identiske vekter. En senere kort test med nye innstillinger ga identiske resultater etter 12 oppdateringer.', 'file': 'replay-verification.json'},
        {'title': 'Ferdig versjon', 'text': 'Modellversjonen fra epoke 3 er eksportert og kjøres lokalt i Ollama. Kontrollene er utført av samme prosjektoperatør; de er ikke en ekstern sertifisering.', 'file': 'serving-package.json'},
    ]
    if condition == 'A':
        return {'title': 'Dokumentasjon', 'steps': [], 'text': 'Ingen ekstra modelldokumentasjon vises i denne versjonen.'}
    return {'title': 'Modellens utviklingshistorie',
            'text': 'Dette er dokumentasjon om demonstrasjonsmodellen. Rådet i oppgaven er en fast tekst skrevet for forsøket.',
            'steps': [{**{k: v for k, v in s.items() if k != 'file'},
                       **({'evidence': '/evidence/' + s['file']} if condition == 'C' else {})} for s in steps]}


class Study:
    def __init__(self, root, protocol, interface_files, evidence_root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.protocol = json.loads(Path(protocol).read_text(encoding='utf-8'))
        self.cases = {c['id']: c for c in self.protocol['cases']}
        self.docs = {c: documentation(c) for c in 'ABC'}
        self.interface = {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in interface_files}
        self.evidence = {}
        for filename in ('base-lock.json', 'verification.json', 'training-summary.json', 'replay-verification.json', 'serving-package.json'):
            path = Path(evidence_root)/filename
            if path.is_file():
                raw = path.read_bytes()
                self.evidence[filename] = hashlib.sha256(raw).hexdigest()
                target = self.root/'evidence'/self.evidence[filename]
                target.parent.mkdir(exist_ok=True)
                if not target.exists(): target.write_bytes(raw)
        for step in self.docs['C']['steps']:
            filename = step['evidence'].rsplit('/', 1)[-1]
            if filename in self.evidence:
                step['evidence'] = '/study-evidence/' + self.evidence[filename]
            else:
                del step['evidence']
        self.snapshot = {'protocol': self.protocol, 'documentation': self.docs, 'interface': self.interface, 'evidence': self.evidence}
        self.version = digest(self.snapshot)
        snapshot = self.root/(self.version + '.json')
        if not snapshot.exists(): snapshot.write_text(encoded(self.snapshot), encoding='utf-8')
        self.db = self.root/'study.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS allocations (version TEXT, position INTEGER, cell TEXT, PRIMARY KEY(version, position));
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, secret_hash TEXT, version TEXT, created TEXT, assignment TEXT);
                CREATE TABLE IF NOT EXISTS trials (session TEXT, ordinal INTEGER, payload TEXT, presented TEXT, response TEXT, submitted TEXT, PRIMARY KEY(session, ordinal));
                CREATE TABLE IF NOT EXISTS events (session TEXT, ordinal INTEGER, kind TEXT, at TEXT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def start(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            n = db.execute('SELECT count(*) FROM sessions WHERE version=?', (self.version,)).fetchone()[0]
            if n % 24 == 0:
                cells = [(c, v) for c in 'ABC' for v in itertools.product(('good', 'bad'), repeat=3)]
                secrets.SystemRandom().shuffle(cells)
                db.executemany('INSERT INTO allocations VALUES(?,?,?)', [(self.version, n+i, encoded(cell)) for i, cell in enumerate(cells)])
            condition, variants = json.loads(db.execute('SELECT cell FROM allocations WHERE version=? AND position=?', (self.version, n)).fetchone()[0])
            order = list(self.cases)
            secrets.SystemRandom().shuffle(order)
            assignment = {'condition': condition, 'variants': dict(zip(self.cases, variants)), 'order': order, 'allocation': n}
            sid, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (sid, digest(token), self.version, utc(), encoded(assignment)))
            for i, case_id in enumerate(order):
                payload = self.payload(case_id, assignment['variants'][case_id], condition, i)
                db.execute('INSERT INTO trials VALUES(?,?,?,?,?,?)', (sid, i, encoded(payload), None, None, None))
        return {'session_id': sid, 'session_token': token}

    def payload(self, case_id, variant, condition, ordinal=0):
        c = self.cases[case_id]
        return {'ordinal': ordinal, 'total': 3, 'case_id': case_id, 'title': c['title'], 'situation': c['situation'],
                'options': c['options'], 'advice': c['advice'][variant], 'advice_sha256': digest(c['advice'][variant]),
                'scenario_sha256': digest({k: c[k] for k in ('id', 'title', 'situation', 'options')}),
                'documentation': self.docs[condition], 'documentation_sha256': digest(self.docs[condition]), 'protocol_sha256': self.version}

    def authenticate(self, db, sid, token):
        row = db.execute('SELECT * FROM sessions WHERE id=?', (sid,)).fetchone()
        if row is None or not secrets.compare_digest(row['secret_hash'], digest(token)):
            raise PermissionError('Ukjent økt. Start fra informasjonssiden.')
        return row

    def current(self, sid, token):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.authenticate(db, sid, token)
            row = db.execute('SELECT * FROM trials WHERE session=? AND response IS NULL ORDER BY ordinal LIMIT 1', (sid,)).fetchone()
            if row is None: return {'complete': True}
            db.execute('UPDATE trials SET presented=COALESCE(presented,?) WHERE session=? AND ordinal=?', (utc(), sid, row['ordinal']))
            return json.loads(row['payload'])

    def submit(self, sid, token, ordinal, response):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.authenticate(db, sid, token)
            row = db.execute('SELECT * FROM trials WHERE session=? AND ordinal=?', (sid, ordinal)).fetchone()
            if row is None or row['presented'] is None: raise ValueError('Oppgaven er ikke vist.')
            if row['response']:
                if row['response'] == encoded(response): return {'saved': True, 'duplicate': True}
                raise ValueError('Et svar er allerede lagret for oppgaven.')
            if response['decision'] not in [o['id'] for o in json.loads(row['payload'])['options']]: raise ValueError('Velg et av alternativene.')
            db.execute('UPDATE trials SET response=?,submitted=? WHERE session=? AND ordinal=?', (encoded(response), utc(), sid, ordinal))
        return {'saved': True}

    def event(self, sid, token, ordinal, kind):
        with self.connect() as db:
            self.authenticate(db, sid, token)
            row = db.execute('SELECT payload,presented,response FROM trials WHERE session=? AND ordinal=?', (sid, ordinal)).fetchone()
            if not row or not row['presented'] or row['response']: raise ValueError('Ingen åpen oppgave.')
            steps = json.loads(row['payload'])['documentation']['steps']
            if not steps: raise ValueError('Ingen dokumentasjon i denne oppgaven.')
            if kind == 'evidence_open' and not any('evidence' in s for s in steps): raise ValueError('Ingen dokumentlenker.')
            # Bounded interaction counts, never collect URLs, IP addresses or free-form browser telemetry.
            count = db.execute('SELECT count(*) FROM events WHERE session=? AND ordinal=?', (sid, ordinal)).fetchone()[0]
            if count < 100: db.execute('INSERT INTO events VALUES(?,?,?,?)', (sid, ordinal, kind, utc()))

    def export(self):
        with self.connect() as db:
            rows = db.execute('SELECT s.id,s.version,s.created,s.assignment,t.* FROM sessions s JOIN trials t ON t.session=s.id ORDER BY s.created,t.ordinal').fetchall()
            out = []
            for row in rows:
                a, p = json.loads(row['assignment']), json.loads(row['payload'])
                events = [dict(e) for e in db.execute('SELECT kind,at FROM events WHERE session=? AND ordinal=?', (row['id'], row['ordinal']))]
                out.append({'session_id': row['id'], 'protocol_sha256': row['version'], 'created': row['created'],
                            'allocation': a['allocation'], 'condition': a['condition'], 'candidate_quality': a['variants'][p['case_id']],
                            'ordinal': row['ordinal'], 'case_id': p['case_id'], 'advice_sha256': p['advice_sha256'],
                            'scenario_sha256': p['scenario_sha256'], 'documentation_sha256': p['documentation_sha256'],
                            'presented': row['presented'], 'submitted': row['submitted'],
                            'elapsed_seconds': (datetime.fromisoformat(row['submitted'])-datetime.fromisoformat(row['presented'])).total_seconds() if row['submitted'] else None,
                            'response': json.loads(row['response']) if row['response'] else None, 'events': events,
                            'review_status': 'provisional; pilot-only'})
            return out
