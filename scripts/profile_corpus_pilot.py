"""Run selected isolated local token Blueprints over the same hash-locked 13 books.

Requires fetch_tokenizer_candidates.py and public newcomer input exports. No
network writes, training, model-weight downloads or consensus activation.
"""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dawl_node.common import canonical_json, keygen, load_key, read_json, sha256
from dawl_profiles.profiles import make_profile, make_o200k_profile, ProfileTokenizer
from dawl_profiles.workspace import Workspace, ProfileLedger, source_frags

SMOKE = {
    'empty': '', 'whitespace': '  \t\r\n\n',
    'norwegian': 'Hei! Vi bygger et \u00e5pent nettverk for spr\u00e5kmodeller. \u00c6\u00d8\u00c5 \u00e6\u00f8\u00e5.',
    'combining': 'cafe\u0301 A\u030a \u00c5',
    'cjk': '\u4f60\u597d\uff0c\u4e16\u754c\u3002\u65e5\u672c\u8a9e\u3068\ud55c\uad6d\uc5b4',
    'emoji': '\U0001f469\u200d\U0001f4bb \U0001f1f3\U0001f1f4 \U0001f30d',
    'code': 'def f(x):\n\treturn x * 123456789\n',
    'literal_controls': '<|endoftext|><|im_start|><|begin_of_text|><think>literal</think>',
    'null': 'left\x00right',
}


def corpus():
    base = read_json(ROOT/'research/corpora/classics-8-v1.lock.json')
    extra = read_json(ROOT/'artifacts/multiprofile-inputs/scifi.lock.json')
    result = []
    for lock, folder in ((base, ROOT/'artifacts/platform-v1/jobs'),
                         (extra, ROOT/'artifacts/multiprofile-inputs')):
        for book in lock['books']:
            path = folder/str(book['id'])/'input.jsonl'
            raw = b''.join(json.loads(line)['text'].encode('utf-8') for line in path.read_bytes().splitlines() if line)
            if len(raw) != book['bytes'] or sha256(raw) != book['sha256']:
                raise ValueError('public input differs from original book lock')
            result.append((book, raw))
    return sorted(result, key=lambda pair: pair[0]['id'])


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--root', type=Path, default=ROOT/'artifacts/multiprofile-pilot-001')
    parser.add_argument('--report', type=Path, default=ROOT/'docs/results/multiprofile-run-001/report.json')
    parser.add_argument('--profiles', nargs='+', choices=['gpt2-baseline','smollm2','qwen3','qwen35','smollm3','o200k_base'],
                        default=['gpt2-baseline','smollm2','qwen3','qwen35','smollm3','o200k_base'])
    args = parser.parse_args()
    # A fresh root preserves prior evidence rather than silently overwriting a run.
    args.root.mkdir(parents=True, exist_ok=False)
    ws = Workspace(args.root)
    public = keygen(args.root/'operator.private.pem', args.root/'operator.public.key')
    key = load_key(args.root/'operator.private.pem')
    books = corpus()
    report = {'schema': 'hypertrix.multi-profile-pilot/1', 'started_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Local signed token Blueprints; shared CAS; no vector replay, model weights, training or Oracle activation.',
              'python': platform.python_version(), 'tokenizers': version('tokenizers'),
              'tiktoken': version('tiktoken'),
              'source_books': [{'id': b['id'], 'sha256': b['sha256'], 'bytes': len(raw)} for b, raw in books],
              'source_bytes': sum(len(raw) for _, raw in books), 'profiles': []}
    tok = (ROOT/'artifacts/ingestion-assets/tokenizer.json').read_bytes()
    config = canonical_json({'model_type': 'gpt2', 'vocab_size': 50257, 'n_embd': 768,
                             'scope': 'Minimal fixture config matching the established GPT-2 input-table layout.'})
    candidates = [('gpt2-baseline', tok, config, None)]
    for name in ('smollm2', 'qwen3', 'qwen35', 'smollm3'):
        if name not in args.profiles: continue
        base = ROOT/'artifacts/tokenizer-candidates'/name
        lock = read_json(base/'download-lock.json')
        for filename, desc in lock['files'].items():
            payload = (base/filename).read_bytes()
            if len(payload) != desc['bytes'] or sha256(payload) != desc['sha256']:
                raise ValueError('candidate differs from acquisition lock')
        candidates.append((name, (base/'tokenizer.json').read_bytes(), (base/'config.json').read_bytes(), lock))
    if 'o200k_base' in args.profiles:
        base = ROOT/'artifacts/tokenizer-candidates/o200k_base'
        candidates.append(('o200k_base', (base/'o200k_base.tiktoken').read_bytes(), None, read_json(base/'download-lock.json')))
    candidates = [c for c in candidates if c[0] in args.profiles]
    for name, tok_bytes, conf_bytes, upstream in candidates:
        print('Starting profile '+name, flush=True)
        # This is the canonical logical tensor name for the future imported manifest.
        # It is not a claim that a tensor with this upstream file key was downloaded.
        profile = make_o200k_profile(tok_bytes) if name == 'o200k_base' else make_profile(tok_bytes, conf_bytes, 'input_embedding.weight', name)
        ws.objects.put(tok_bytes)
        if conf_bytes is not None: ws.objects.put(conf_bytes)
        trust = {'schema': 'dawl.trust/1', 'profile_id': profile['id'],
                 'identities': {public: ['source', 'tokenizer']},
                 'scope': 'Local profile-isolation experiment; one operator, no distributed consensus.'}
        ledger, tokenizer = ws.open_profile(profile, trust)
        smoke = {}
        for label, text in SMOKE.items():
            normalized, ids = tokenizer.encode(text)
            smoke[label] = {'tokens': len(ids), 'raw_roundtrip': normalized == text,
                            'input_sha256': sha256(text.encode()), 'normalized_sha256': sha256(normalized.encode())}
        started = perf_counter()
        entries = [ws.ingest_book(ledger, tokenizer, b['id'], raw, key) for b, raw in books]
        ingestion_seconds = perf_counter() - started
        blueprint = ws.publish(ledger, tokenizer, key)
        # Independent new runtime/database handle, same public artifacts and trust.
        replica = ProfileLedger(args.root/'profiles'/profile['id']/'ledger.sqlite', trust, profile, read_only=True)
        replay_tokenizer = ProfileTokenizer(profile, ws.objects.read(profile['body']['tokenizer']['artifact']),
                                           ws.objects.read(profile['body']['model']['config']) if profile['body']['model'] else None)
        replay = ws.verify_blueprint(replica, replay_tokenizer, blueprint)
        per_book = [ws.verify_tokens(replica, replay_tokenizer, e) for e in entries]
        baseline = 2061330
        result = {'name': name, 'profile': profile, 'upstream': upstream, 'blueprint': blueprint,
                  'trust': trust, 'checkpoint': replica.verify_history(), 'tokens': replay['tokens'],
                  'frags': replay['frags'], 'books': replay['books'], 'smoke': smoke, 'per_book': per_book,
                  'token_reduction_vs_gpt2_percent': 100*(1-replay['tokens']/baseline),
                  'declared_embedding_rows': tokenizer.rows if tokenizer.dimension else None,
                  'token_id_space': tokenizer.rows, 'tokenizer_entries': tokenizer.vocab_size,
                  'declared_dimension': tokenizer.dimension,
                  'projected_float32_table_bytes': tokenizer.rows*tokenizer.dimension*4 if tokenizer.dimension else None,
                  'matrix_scope': 'Size projection from config, not downloaded or verified model weights.' if tokenizer.dimension else 'Tokenizer-only: no model weights or embedding dimension claimed.',
                  'ingestion_seconds': ingestion_seconds, 'timing_scope': 'Local tokenization + CAS writes + signatures; no controlled timing benchmark.',
                  'accepted': replay['accepted']}
        if name == 'gpt2-baseline' and (replay['tokens'], replay['frags']) != (2061330, 1807):
            raise ValueError('baseline does not reproduce the historical corpus counts')
        report['profiles'].append(result)
        print(json.dumps({k: result[k] for k in ('name','tokens','frags','token_reduction_vs_gpt2_percent','projected_float32_table_bytes','accepted')}), flush=True)
    # The same signer cannot transplant an otherwise valid signed release to another namespace.
    if len(report['profiles']) >= 2:
        a, b = report['profiles'][:2]
        foreign = ProfileLedger(args.root/'profiles'/b['profile']['id']/'ledger.sqlite', b['trust'], b['profile'], read_only=True)
        try: foreign.validate(a['blueprint'])
        except ValueError: report['cross_profile_blueprint_rejected'] = True
        else: raise ValueError('foreign profile Blueprint accepted')
    all_objects = list(ws.objects.root.iterdir())
    report.update(completed_utc=datetime.now(timezone.utc).isoformat(),
                  unique_objects=len(all_objects), stored_object_bytes=sum(p.stat().st_size for p in all_objects),
                  shared_raw_books=len({b['sha256'] for b,_ in books}),
                  raw_book_references=len(books)*len(candidates),
                  implementation_files={str(p.relative_to(ROOT)).replace('\\','/'):sha256(p.read_bytes())
                      for p in [ROOT/'src/dawl_profiles/profiles.py',ROOT/'src/dawl_profiles/workspace.py',Path(__file__)]})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print('Report: '+str(args.report), flush=True)


if __name__ == '__main__': main()
