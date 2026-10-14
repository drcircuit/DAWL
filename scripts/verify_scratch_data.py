"""Re-read original source bytes and independently reconstruct every Seed token window."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import time
import numpy as np
from tokenizers import Tokenizer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_node.common import keygen,load_key,sign,write_json,sha256
from dawl_scratch.data import load,file_hash


def verify(root,books,code,check_only=False):
    began=time.monotonic();blueprint,arrays=load(root)
    sources=json.loads((root/'sources.json').read_bytes())
    if file_hash(books/'source-lock.json')!=sources['books_lock_sha256'] or file_hash(code/'corpus-lock.json')!=sources['code_lock_sha256']:
        raise ValueError('Original corpus locks changed')
    books_lock=json.loads((books/'source-lock.json').read_bytes())
    book_files={str(b['id']):books/b['file'] for b in books_lock['books']}
    windows={w['id']:w for w in json.loads((root/'windows.json').read_bytes())}
    tok=Tokenizer.from_file(str(root/'tokenizer.json'))
    checked=0;targets=0;expected_row={split:0 for split in arrays};groups={};digests=set()
    for doc in sources['documents']:
        original=book_files[doc['id'].split(':')[1]] if doc['kind']=='book' else code/'objects'/doc['source_sha256']
        raw=original.read_bytes()
        if sha256(raw)!=doc['source_sha256']:raise ValueError('Original source hash differs')
        text=raw[doc['start']:doc['end']]
        if sha256(text)!=doc['text_sha256'] or (root/'texts'/doc['text_sha256']).read_bytes()!=text:
            raise ValueError('Source derivative differs')
        if doc['text_sha256'] in digests:raise ValueError('Duplicate document survived')
        digests.add(doc['text_sha256'])
        if groups.setdefault(doc['group'],doc['split'])!=doc['split']:raise ValueError('Source group crosses holdout')
        ids=tok.encode(text.decode('utf-8'),add_special_tokens=False).ids
        if any(i in (0,1,2) for i in ids):raise ValueError('Reserved control token appeared in source')
        ids.append(blueprint['eos_id']);window=windows[doc['id']];split=doc['split']
        if window['start_row']!=expected_row[split]:raise ValueError('Window mapping is not contiguous')
        count=0;doc_targets=0
        for offset in range(0,len(ids)-1,blueprint['context']):
            sequence=ids[offset:offset+blueprint['context']+1]
            n=len(sequence)-1
            sequence.extend([blueprint['pad_id']]*(blueprint['context']+1-len(sequence)))
            if not np.array_equal(arrays[split][window['start_row']+count],sequence):
                raise ValueError('Retokenized window differs')
            checked+=1;count+=1;targets+=n;doc_targets+=n
        if count!=window['end_row']-window['start_row'] or doc_targets!=window['target_tokens']:
            raise ValueError('Document accounting differs')
        expected_row[split]=window['end_row']
    if any(expected_row[s]!=len(arrays[s]) for s in arrays):raise ValueError('Unmapped windows remain')
    result={'schema':'hypertrix.scratch-data-replay/1','accepted':True,'documents':len(sources['documents']),
        'windows':checked,'targets':targets,'blueprint_sha256':file_hash(root/'blueprint.json'),
        'verification_code_sha256':file_hash(Path(__file__)),'elapsed_seconds':time.monotonic()-began,
        'utc':datetime.now(timezone.utc).isoformat(),
        'checks':['original source SHA256','exact recorded byte-range derivative','whole-source split isolation',
                  'exact document deduplication','all token windows','all target counts'],
        'scope':'Independent verifier process under the same operator; source URL authenticity and rights are external metadata.'}
    if not check_only:
        directory=root.parent/'data-verifier';directory.mkdir(exist_ok=True)
        keygen(directory/'key.private.pem',directory/'public.key')
        write_json(directory/'receipt.json',sign(result,load_key(directory/'key.private.pem')))
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--books',type=Path,required=True);p.add_argument('--code',type=Path,required=True)
    p.add_argument('--check-only',action='store_true',help='Verify read-only, without creating keys or a new receipt')
    a=p.parse_args();verify(a.root,a.books,a.code,a.check_only)
