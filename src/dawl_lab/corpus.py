"""Curate inert, attributed source objects before constructing a token Blueprint.

Downloads are pinned at first acquisition. Nothing fetched is imported or run.
The source lock is immutable; interrupted downloads can be resumed safely.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import time
import threading
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from dawl_corpus.github import SECRET, fetch_all, content_reason
from dawl_node.common import immutable_write, write_json, sha256
from dawl_seed.progress import progress
from dawl_scratch.data import file_hash

_mirror_lock=threading.Lock()
_mirror_last=0.0


def fetch(root, url, limit=1024**3):
    """Content-address downloads, retaining exact requested URL and response digest."""
    key = sha256(url.encode())
    record = root/'downloads'/f'{key}.json'
    if record.exists():
        meta = json.loads(record.read_bytes()); path = root/'raw'/meta['sha256']
        if path.stat().st_size != meta['bytes'] or file_hash(path) != meta['sha256']:
            raise ValueError('Cached download integrity failure')
        return path, meta
    root.joinpath('raw').mkdir(parents=True, exist_ok=True)
    pending = root/'raw'/f'{key}.partial'
    for attempt in range(3):
        try:
            if urllib.parse.urlsplit(url).netloc=='gutenberg.pglaf.org':
                global _mirror_last
                with _mirror_lock:
                    time.sleep(max(0,2-(time.monotonic()-_mirror_last)))
                    _mirror_last=time.monotonic()
            request = urllib.request.Request(url, headers={'User-Agent': 'Hypertrix-research-corpus/1'})
            with urllib.request.urlopen(request, timeout=90) as response, pending.open('wb') as out:
                digest = hashlib.sha256(); count = 0
                while chunk := response.read(1024*1024):
                    count += len(chunk)
                    if count > limit: raise ValueError('Download exceeds declared bound')
                    digest.update(chunk); out.write(chunk)
            name = digest.hexdigest(); target = root/'raw'/name
            if target.exists():
                if file_hash(target) != name: raise ValueError('Corrupt existing raw object')
                pending.unlink()
            else: pending.replace(target)
            meta = {'url': url, 'sha256': name, 'bytes': count}
            write_json(record, meta)
            return target, meta
        except Exception:
            if attempt == 2: raise
            time.sleep(2*(attempt+1))


def normalized(text):
    return unicodedata.normalize('NFC', text).replace('\r\n', '\n').replace('\r', '\n').strip()


def text_flag(text, code=False):
    if len(text) < (80 if code else 600): return 'too-short'
    if len(text) > 8_000_000: return 'too-long'
    if any(s in text for s in ('<|bos|>', '<|eos|>', '<|pad|>')): return 'reserved-token'
    if SECRET.search(text.encode()): return 'credential-pattern'
    if '\x00' in text or '\ufffd' in text: return 'invalid-text'
    if not code:
        if sum(c.isalpha() for c in text)/len(text) < .45: return 'low-letter-ratio'
        lines = [line.strip() for line in text.splitlines() if len(line.strip()) > 30]
        if len(lines) > 10 and len(set(lines))/len(lines) < .55: return 'repeated-lines'
    return None


def book_text(raw):
    text = raw.decode('utf-8-sig')
    start = re.search(r'\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*', text, re.I)
    end = re.search(r'\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*', text, re.I)
    if not start or not end or start.end() >= end.start(): raise ValueError('Missing Gutenberg boundaries')
    text = text[start.end():end.start()]
    # Join printer line wraps within a paragraph, retaining paragraph boundaries.
    return normalized('\n\n'.join(re.sub(r'\s*\n\s*', ' ', p).strip() for p in re.split(r'\n\s*\n', text)))


def books(root, plan):
    base = plan['gutenberg_mirror']
    catalog, _ = fetch(root, base+'/cache/epub/feeds/pg_catalog.csv.gz', 32*1024**2)
    with gzip.open(catalog, 'rt', encoding='utf-8-sig', newline='') as f:
        entries = {int(r['Text#']): r for r in csv.DictReader(f) if r.get('Text#','').isdigit()}
    rows, excluded = [], []
    ns = {'dct': 'http://purl.org/dc/terms/', 'pg': 'http://www.gutenberg.org/2009/pgterms/',
          'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'}
    def one(item):
            domain,book_id=item
            meta = entries.get(book_id, {})
            try:
                if meta.get('Language') != 'en': raise ValueError('Not catalogued as English')
                # Record metadata for review, including every author/translator in RDF.
                rdf, rd = fetch(root, f'{base}/cache/epub/{book_id}/pg{book_id}.rdf', 2*1024**2)
                tree = ET.fromstring(rdf.read_bytes())
                rights = [x.text for x in tree.findall('.//dct:rights', ns)]
                agents = []
                for a in tree.findall('.//pg:agent', ns):
                    name = a.findtext('pg:name', namespaces=ns)
                    death = a.findtext('pg:deathdate', namespaces=ns)
                    agents.append({'name': name, 'death': int(death) if death and re.fullmatch(r'-?\d+', death) else None})
                if not rights or not any('Public domain' in r for r in rights): raise ValueError('Rights review required')
                if not agents or any(a['death'] is None or a['death'] > 1945 for a in agents):
                    raise ValueError('Contributor dates require review')
                path, download = fetch(root, f'{base}/cache/epub/{book_id}/pg{book_id}.txt', 16*1024**2)
                text = book_text(path.read_bytes())
                row={'id': f'gutenberg:{book_id}', 'group': 'book-author:'+meta['Authors'].split(';')[0].strip(),
                    'domain': domain, 'language': 'en', 'text': text, 'title': meta['Title'],
                    'origin': {'url': f'https://www.gutenberg.org/ebooks/{book_id}', 'download': download,
                               'catalog': meta, 'rdf': rd, 'contributors': agents},
                    'rights': {'publisher_statement': rights, 'basis': 'Public domain catalog statement; listed contributors died by 1945',
                               'metadata_url': f'https://www.gutenberg.org/ebooks/{book_id}'}}
                print(json.dumps({'book': book_id, 'title': meta['Title'], 'characters': len(text)}), flush=True)
                return row,None
            except Exception as error:
                rejection={'id': f'gutenberg:{book_id}', 'title': meta.get('Title'),
                           'reason': str(error) if isinstance(error, ValueError) else type(error).__name__}
                print(json.dumps({'book_excluded':rejection}),flush=True)
                return None,rejection
    items=[];seen=set()
    for domain,ids in [('books_fiction',plan['gutenberg_fiction']),('books_nonfiction',plan['gutenberg_nonfiction'])]:
        for book_id in ids:
            if book_id not in seen:items.append((domain,book_id));seen.add(book_id)
    # Four in-flight transfers, but all new mirror requests share a two-second gate.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row,rejection in pool.map(one,items):
            if row:rows.append(row)
            if rejection:excluded.append(rejection)
    return rows, excluded


def wikipedia(root, plan):
    import pyarrow.parquet as pq
    repo = 'https://huggingface.co/datasets/wikimedia/wikipedia/resolve/'+plan['wikipedia_revision']+'/'
    _, license_snapshot = fetch(root, repo+'README.md', 1024**2)
    totals = Counter(); rows = []; excluded = []
    files_by_language=Counter(f.split('/')[0].split('.')[1] for f in plan['wikipedia_files'])
    for filename in plan['wikipedia_files']:
        lang = filename.split('/')[0].split('.')[1]
        file_characters=0
        file_cap=plan['wikipedia_max_characters'][lang]//files_by_language[lang]
        path, download = fetch(root, repo+filename)
        for batch in pq.ParquetFile(path).iter_batches(batch_size=256):
            for doc in batch.to_pylist():
                if file_characters >= file_cap: break
                text = normalized(doc['text'])
                # Keep prose, and stop before reference/link/navigation appendices.
                text = re.split(r'\n(?:References|External links|Further reading|Referanser|Eksterne lenker|Kjelder|Bakgrunnsstoff)\s*\n', text)[0].strip()
                reason = text_flag(text)
                if doc['title'].startswith(('List of ', 'Liste over ', 'Liste av ')): reason = 'list-page'
                if reason: continue
                identity = f'wikipedia:{lang}:{doc["id"]}'
                rows.append({'id': identity, 'group': identity, 'domain': 'web_no' if lang in ('no','nn') else 'web_en',
                    'language': lang, 'title': doc['title'], 'text': text,
                    'origin': {'url': doc['url'], 'history_url': doc['url']+'?action=history', 'article_id': doc['id'],
                               'dump': '20231101', 'repository_revision': plan['wikipedia_revision'], 'download': download},
                    'rights': {'license': 'CC-BY-SA-3.0 (dataset declaration)', 'attribution': 'Wikipedia contributors; article and history links retained',
                               'license_snapshot': license_snapshot, 'transform': 'NFC, normalized newlines, terminal reference sections removed'}})
                totals[lang] += len(text)
                file_characters += len(text)
            if file_characters >= file_cap: break
        print(json.dumps({'wikipedia': filename, 'characters_by_language': dict(totals), 'documents': len(rows)}), flush=True)
    return rows, excluded


def parliament(root, plan):
    revision = plan['npsc_revision']
    repo = 'https://huggingface.co/datasets/NbAiLab/NPSC/resolve/'+revision+'/'
    api, _ = fetch(root, 'https://huggingface.co/api/datasets/NbAiLab/NPSC/revision/'+revision, 4*1024**2)
    _, license_snapshot = fetch(root, repo+'README.md', 1024**2)
    rows = []
    for entry in json.loads(api.read_bytes())['siblings']:
        filename = entry['rfilename']
        if not filename.startswith('data/') or not filename.endswith('.json'): continue
        path, download = fetch(root, repo+filename, 32*1024**2)
        values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        values.sort(key=lambda x: x['sentence_order'])
        group = 'npsc:'+Path(filename).stem; segments = []; current = []
        speaker = None; language = None
        for value in values:
            if value['speaker_id'] != speaker or value['sentence_language_code'] != language:
                if current: segments.append((language, current))
                current=[]; speaker=value['speaker_id']; language=value['sentence_language_code']
            # Only original human transcription; never translated counterpart.
            current.append(value['normsentence_text'])
        if current: segments.append((language,current))
        split = {'eval':'validation','test':'test','train':'train'}[filename.split('/')[1]]
        for i,(lang,sentences) in enumerate(segments):
            if lang not in ('nb-NO','nn-NO'): continue
            text = normalized('. '.join(s.strip().rstrip('.') for s in sentences)+'.')
            if text_flag(text): continue
            rows.append({'id': f'{group}:{i}', 'group': group, 'split': split, 'domain': 'parliament_no',
                'language': lang, 'text': text, 'title': 'Stortinget '+Path(filename).stem,
                'origin': {'url': 'https://www.nb.no/sprakbanken/ressurskatalog/oai-nb-no-sbr-84/',
                           'repository_revision': revision, 'download': download, 'segment': i, 'field': 'normsentence_text'},
                'rights': {'license':'CC0-1.0','license_snapshot':license_snapshot,'transform':'Consecutive original-language speaker turns; sentence separators added'}})
    return rows, []


def code(root, plan):
    repo_root = root/'github'
    report = fetch_all(plan['github_owner'], repo_root, workers=3)
    if report['statuses'].get('fetch-error'): raise ValueError('Repository refresh incomplete; retry before freezing')
    rows=[]; excluded=[]
    for path in sorted((repo_root/'repositories').glob('*/snapshot.json')):
        snapshot=json.loads(path.read_bytes())
        if snapshot['status'] != 'candidate': continue
        for doc in snapshot['files']:
            text=(repo_root/'objects'/doc['sha256']).read_text(encoding='utf-8')
            flag = text_flag(text, code=True)
            # A training corpus should not be dominated by captured reports or machine output.
            if any(p in ('output','artifacts','results','evidence','screenshots') for p in Path(doc['path']).parts): flag='generated-experiment-output'
            if flag: excluded.append({'id':snapshot['full_name']+'/'+doc['path'],'reason':flag});continue
            rows.append({'id':snapshot['full_name']+'/'+doc['path'],'group':'github:'+snapshot['family'],
                'domain':'code','language':'code','text':text,'title':doc['path'],
                'origin':{'url':doc['source_url'],'repository':snapshot['full_name'],'commit':snapshot['commit'],
                          'file':doc['path'],'raw_sha256':doc['sha256'],'snapshot_sha256':file_hash(path)},
                'rights':{'license':snapshot.get('license'),'basis':snapshot['rights_basis'], 'license_files':snapshot['licenses']}})
    return rows,excluded


def acquire(root, plan_path, previous=None):
    root.mkdir(parents=True,exist_ok=True)
    if (root/'source-lock.json').exists(): raise ValueError('Frozen corpus exists; choose a new root')
    plan=json.loads(plan_path.read_bytes());write_json(root/'plan.json',plan)
    # Independent source families, bounded concurrency; Gutenberg itself is rate limited.
    all_rows=[];exclusions=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={name:pool.submit(fn,root/name,plan) for name,fn in [('books',books),('wiki',wikipedia),('parliament',parliament),('code',code)]}
        for name,job in jobs.items():
            rows,excluded=job.result();all_rows.extend(rows);exclusions.extend(excluded)
            progress(root,'Curating sources',job='Seed III corpus',role='tokenizer',status='running',
                     completed=len(all_rows),total=0,unit='documents',source=name)
    prior={};old=[]
    if previous:
        old=json.loads((previous/'sources.json').read_bytes())['documents']
        priority={'train':0,'validation':1,'test':2}
        for d in old:
            prior[d['group']]=max(prior.get(d['group'],'train'),d['split'],key=priority.get)
            if d['domain']=='norwegian':
                all_rows.append({**d,'domain':'historical_no','language':'historical-no',
                    'text':(previous/'texts'/d['text_sha256']).read_text(encoding='utf-8'),
                    'rights':{'license':'CC0-1.0','basis':'Pinned bokselskap.no 2023 distribution'}})
        # All new books by an author inherit the strictest old split of that author.
        old_book={d['id']:d['split'] for d in old if d['id'].startswith('gutenberg:')}
        for d in all_rows:
            if d['id'] in old_book:
                prior[d['group']]=max(prior.get(d['group'],'train'),old_book[d['id']],key=priority.get)
    for d in all_rows:
        bucket=int(sha256(('lab-split-v1/'+d['group']).encode())[:8],16)%100
        d['split']=prior.get(d['group'],d.get('split','test' if bucket<5 else 'validation' if bucket<10 else 'train'))
    # Holdouts own duplicates; normalize before comparing. Leave whole documents intact.
    all_rows.sort(key=lambda d:({'test':0,'validation':1,'train':2}[d['split']],d['id']))
    seen=set();holdout_paragraphs=set();admitted=[]
    for d in all_rows:
        text=normalized(d.pop('text'));flag=text_flag(text,d['domain']=='code')
        digest=sha256(text.encode())
        paragraphs=[p.strip() for p in text.split('\n\n') if len(p.strip())>=200]
        para=[(sha256(re.sub(r'\s+',' ',p).encode()),len(p)) for p in paragraphs]
        if digest in seen: flag='exact-normalized-duplicate'
        if d['split']=='train' and sum(n for h,n in para if h in holdout_paragraphs)>len(text)*.2:
            flag='substantial-exact-paragraph-overlap-with-holdout'
        if flag:exclusions.append({'id':d['id'],'reason':flag});continue
        if d['split']!='train':holdout_paragraphs.update(h for h,n in para)
        seen.add(digest);immutable_write(root/'texts'/digest,text.encode())
        admitted.append({**d,'text_sha256':digest,'characters':len(text),'bytes':len(text.encode())})
    write_json(root/'sources.json',{'documents':admitted,'exclusions':exclusions})
    counts=Counter((d['domain'],d['split']) for d in admitted)
    for domain in plan['mixture']:
        if any(counts[(domain,s)]==0 for s in ('train','validation','test')):
            raise ValueError('Missing split for domain '+domain)
    lock={'schema':'hypertrix.lab-source-lock/1','plan_sha256':file_hash(root/'plan.json'),
          'sources_sha256':file_hash(root/'sources.json'),'documents':len(admitted),'excluded':len(exclusions),
          'counts':{f'{d}/{s}':n for (d,s),n in sorted(counts.items())},
          'characters':{domain:sum(d['characters'] for d in admitted if d['domain']==domain) for domain in plan['mixture']},
          'split_policy':'Whole book-author, upstream-repository, Wikipedia article or parliamentary meeting; historical author families and earlier held-out groups preserved.',
          'deduplication':'NFC/newline normalized exact documents; exclude training documents with >20% exact paragraphs shared with held-out documents. Near-duplicate coverage is bounded.',
          'previous_sources_sha256':file_hash(previous/'sources.json') if previous else None}
    write_json(root/'source-lock.json',lock)
    progress(root,'Corpus curated',job='Seed III corpus',role='tokenizer',status='complete',completed=len(admitted),total=len(admitted),unit='documents')
    return lock


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);p.add_argument('--previous',type=Path)
    a=p.parse_args();print(json.dumps(acquire(a.root,a.plan,a.previous)))
