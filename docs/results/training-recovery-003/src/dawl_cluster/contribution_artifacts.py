"""Build and independently check a public signed contribution package."""
from pathlib import Path
from dawl_node.common import canonical_json,sha256,verify_signature
from dawl_platform.ledger import Ledger
from dawl_platform.workflow import implementation_digest


def catalog(ledger,objects):
    books=[];jobs=[];records=[];embedding=None
    vectors=sorted((e for e in ledger.entries() if e['body']['kind']=='vectors'),key=lambda e:e['body']['payload']['book_id'])
    for vector in vectors:
        vp=vector['body']['payload'];token=ledger.get(vp['tokens_entry'])['body']['payload']
        source=ledger.get(token['source_entry'])['body']['payload']
        table=ledger.get(vp['table_entry'])['body']['payload']['descriptor']
        if embedding is not None and table!=embedding:raise ValueError('mixed embedding tables')
        embedding=table
        index=objects.read_json(vp['files']['index'])
        # New books use stable per-book assignment, so later additions never reshuffle old splits.
        value=int(sha256(('hypertrix-split-v1:'+str(vp['book_id'])).encode())[:8],16)%10
        split='test' if value==0 else 'validation' if value==1 else 'train'
        books.append(dict(book_id=vp['book_id'],vectors_entry=vector['id'],tokens_entry=vp['tokens_entry'],
            chunks=vp['chunks'],tokens=vp['tokens'],split=split,title=source['title'],source_sha256=source['files']['book']['sha256']))
        for start in range(0,len(index),64):
            chosen=index[start:start+64];dense=sum(r['vector_bytes'] for r in chosen)
            spec=dict(kind='full-shard-replay/v1',vectors_entry=vector['id'],book_id=vp['book_id'],
                      positions=[r['source_index'] for r in chosen],tokens=dense//(4*table['shape'][1]),dense_bytes=dense)
            jobs.append({'id':sha256(canonical_json(spec)),'spec':spec})
        manifest=objects.read_json(token['files']['manifest.json'])['body']
        for shard in manifest['shards']:
            for row in objects.read_json(token['files'][shard['name']+'.records.json']):
                records.append(dict(book_id=vp['book_id'],source_index=row['source_index'],split=split,
                    file=token['files'][shard['name']+'.u32le'],offset=row['token_offset'],count=row['token_count'],
                    tokens_sha256=index[row['source_index']]['tokens_sha256'],tokens_entry=vp['tokens_entry']))
    if not embedding:raise ValueError('no vector lineages')
    manifest=dict(schema='dawl.training-input/1',dtype='uint32-le',tokenizer_sha256=embedding['tokenizer_sha256'],
        vocab_size=embedding['shape'][0],records=records,split_contract='stable-book-hash-modulo/v1',
        sequence_contract='within-source-chunk; next-token targets; pad with masked EOS; never cross split or chunk',
        tokens=sum(b['tokens'] for b in books),chunks=len(records))
    return dict(books=books,jobs=jobs,lineage_checkpoint=ledger.checkpoint(),training_manifest=objects.json(manifest),
                implementation_sha256=implementation_digest(),tokenizer_sha256=embedding['tokenizer_sha256'],embedding_sha256=embedding['sha256'])


def import_manifest(root,envelope,contribution,anchors):
    body=verify_signature(envelope,contribution['provider'])
    if set(body)!={'schema','catalog','trust','lineage','signer'} or body['schema']!='hypertrix.contribution-manifest/1':
        raise ValueError('invalid signed contribution manifest')
    if body['catalog']!=contribution['catalog']:raise ValueError('manifest differs from committed catalog')
    trust=body['trust']
    if len(trust['identities'])>64 or not 1<=len(body['lineage'])<=256:raise ValueError('lineage exceeds bounds')
    for field in ('embedding_sha256','tokenizer_sha256'):
        if trust['anchors'][field]!=anchors[field] or body['catalog'][field]!=anchors[field]:
            raise ValueError('contributor attempted to replace network transform')
    ledger=Ledger(Path(root)/'ledger.sqlite',trust)
    for entry in body['lineage']:ledger.add(entry)
    if ledger.verify_history()!=contribution['catalog']['lineage_checkpoint']:raise ValueError('contribution lineage differs from checkpoint')
    return ledger
