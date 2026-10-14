"""Append-only contribution admission. Pure consensus rules; no network I/O."""
import ipaddress
import re
from urllib.parse import urlsplit
from dawl_node.common import canonical_json, sha256
from data_ingestion.protocol import require_digest

CONTRIBUTOR = 'CONTRIBUTOR'


def descriptor(value, maximum=4*1024*1024):
    if set(value)!={'sha256','bytes'} or type(value['bytes']) is not int or not 1<=value['bytes']<=maximum:
        raise ValueError('invalid bounded artifact descriptor')
    require_digest(value['sha256'])


def endpoint(value):
    uri=urlsplit(value)
    if uri.scheme!='http' or uri.port!=8900 or uri.path or uri.query or uri.fragment or uri.username or uri.password:
        raise ValueError('unsupported provider route')
    address=ipaddress.IPv4Address(uri.hostname)
    if address not in ipaddress.ip_network('127.70.0.0/24') or int(address.packed[-1]) not in range(22,30):
        raise ValueError('route outside invited contributor transport range')
    return value


def all_books(state):
    return state['plan']['books']+[b for c in state.get('contributions',{}).values() for b in c['catalog']['books']]


def actor_hats(state, public):
    return state['plan']['actors'].get(public,{}).get('hats', [CONTRIBUTOR] if public in state.get('providers',{}) else [])


def recipe(state, job):
    batch=job.get('contribution')
    return state['contributions'][batch]['catalog']['implementation_sha256'] if batch else state['plan']['implementation_sha256']


def activate(state,payload,tx_id):
    if state.get('protocol_upgrade') or not state['dataset_release'] or any(j['leases'] for j in state['jobs'].values()):
        raise ValueError('upgrade requires a released idle baseline and is one-time')
    if set(payload)!={'version','implementation_sha256','runtime_manifest','tokenizer_sha256','embedding_sha256'} or payload['version']!=2:
        raise ValueError('unsupported protocol activation')
    for field in ('implementation_sha256','runtime_manifest','tokenizer_sha256','embedding_sha256'): require_digest(payload[field])
    state.update(protocol_upgrade={**payload,'transaction':tx_id,'height':state['height']},providers={},contributions={},
                 dataset_releases=[state['dataset_release']])
    return {'protocol_version':2}


def announce(state,public,payload,tx_id):
    if not state.get('protocol_upgrade') or public in state['actors'] or len(state['actors'])>=64 or len(state['providers'])>=8:
        raise ValueError('provider announcement not permitted')
    if set(payload)!={'name','endpoint','capabilities'} or payload['capabilities']!=['PROVIDER','TOKENIZER']:
        raise ValueError('invalid contributor roles')
    if not re.fullmatch('[a-z][a-z0-9-]{0,39}',payload['name']): raise ValueError('invalid contributor name')
    endpoint(payload['endpoint'])
    names={a['name'] for a in state['plan']['actors'].values()}|{p['name'] for p in state['providers'].values()}
    if payload['name'] in names or any(p['endpoint']==payload['endpoint'] for p in state['providers'].values()):
        raise ValueError('duplicate provider name or route')
    state['providers'][public]={**payload,'height':state['height'],'transaction':tx_id}
    state['actors'][public]={'sequence':0,'last_tx':None,'last_result':None}
    return {'provider':public,'status':'announced'}


def validate_catalog(state,catalog):
    fields={'books','jobs','lineage_checkpoint','training_manifest','implementation_sha256','tokenizer_sha256','embedding_sha256'}
    if set(catalog)!=fields: raise ValueError('invalid contribution catalog fields')
    upgrade=state['protocol_upgrade']
    for field in ('implementation_sha256','tokenizer_sha256','embedding_sha256'):
        if catalog[field]!=upgrade[field]: raise ValueError('contribution transform differs from active recipe')
    descriptor(catalog['training_manifest'])
    cp=catalog['lineage_checkpoint']
    if set(cp)!={'policy','entries','set_sha256'} or type(cp['entries']) is not int or not 1<=cp['entries']<=256:
        raise ValueError('invalid lineage checkpoint')
    require_digest(cp['policy']);require_digest(cp['set_sha256'])
    books=catalog['books'];jobs=catalog['jobs']
    if not 1<=len(books)<=8 or not 1<=len(jobs)<=128 or len(state['jobs'])+len(jobs)>10000:
        raise ValueError('contribution exceeds batch bounds')
    prior={b['book_id'] for b in all_books(state)};coverage={};totals={};ids=set()
    for book in books:
        if set(book)!={'book_id','vectors_entry','tokens_entry','chunks','tokens','split','title','source_sha256'}:
            raise ValueError('invalid book fields')
        if type(book['book_id']) is not int or book['book_id'] in prior or not 1<=book['book_id']<=1000000:
            raise ValueError('duplicate or invalid book identity')
        prior.add(book['book_id'])
        if (type(book['chunks']) is not int or not 1<=book['chunks']<=2048 or type(book['tokens']) is not int
                or not 1<=book['tokens']<=4*1024*1024 or book['split'] not in ('train','validation','test')
                or not isinstance(book['title'],str) or not 1<=len(book['title'])<=300): raise ValueError('invalid book bounds')
        for field in ('vectors_entry','tokens_entry','source_sha256'):require_digest(book[field])
        if book['vectors_entry'] in coverage: raise ValueError('duplicate vector root')
        coverage[book['vectors_entry']]=set();totals[book['vectors_entry']]=0
    for item in jobs:
        if set(item)!={'id','spec'}:raise ValueError('invalid job fields')
        spec=item['spec'];identity=item['id']
        if identity!=sha256(canonical_json(spec)) or identity in ids or identity in state['jobs']:
            raise ValueError('duplicate or modified job')
        ids.add(identity)
        if set(spec)!={'kind','vectors_entry','book_id','positions','tokens','dense_bytes'} or spec['kind']!='full-shard-replay/v1':
            raise ValueError('invalid replay recipe')
        book=next(b for b in books if b['vectors_entry']==spec['vectors_entry'])
        positions=spec['positions'];seen=coverage[book['vectors_entry']]
        if (spec['book_id']!=book['book_id'] or not 1<=len(positions)<=256 or positions!=sorted(set(positions))
            or any(type(p) is not int or not 0<=p<book['chunks'] for p in positions) or seen.intersection(positions)):
            raise ValueError('overlap or invalid shard coverage')
        if type(spec['tokens']) is not int or not 1<=spec['tokens']<=4*1024*1024 or type(spec['dense_bytes']) is not int or spec['dense_bytes']!=spec['tokens']*4*state['plan']['embedding_dimension']:
            raise ValueError('invalid shard totals')
        seen.update(positions);totals[book['vectors_entry']]+=spec['tokens']
    for book in books:
        if coverage[book['vectors_entry']]!=set(range(book['chunks'])) or totals[book['vectors_entry']]!=book['tokens']:
            raise ValueError('missing corpus coverage or incorrect totals')


def contribute(state,public,payload,tx_id):
    if public not in state.get('providers',{}) or len(state.get('contributions',{}))>=32:
        raise ValueError('announced contributor required')
    if set(payload)!={'manifest','catalog'}:raise ValueError('invalid contribution fields')
    descriptor(payload['manifest']);validate_catalog(state,payload['catalog'])
    identity=sha256(canonical_json({'provider':public,**payload}))
    state['contributions'][identity]={**payload,'provider':public,'transaction':tx_id,'height':state['height']}
    for item in payload['catalog']['jobs']:
        state['jobs'][item['id']]={'spec':item['spec'],'contribution':identity,'leases':{},'attestations':{},'failures':[],'attempts':0}
    return {'contribution':identity,'jobs':len(payload['catalog']['jobs']),'status':'awaiting-independent-audits'}


def publish(state,height):
    previous=state['dataset_release']
    batches=sorted(state['contributions'])
    if not batches or previous['body'].get('contributions')==batches:return {'dataset_release':previous['id']}
    release={'schema':'dawl.dataset-release/2','plan_sha256':state['plan_sha256'],'parent_release':previous['id'],
             'contributions':batches,'lineage_checkpoints':[state['plan']['lineage_checkpoint']]+[state['contributions'][i]['catalog']['lineage_checkpoint'] for i in batches],
             'training_manifests':[state['plan']['training_manifest']]+[state['contributions'][i]['catalog']['training_manifest'] for i in batches],
             'books':all_books(state),'replicas':state['plan']['replicas'],
             'receipts':sorted(a['transaction'] for j in state['jobs'].values() for a in j['attestations'].values()),
             'scope':'Append-only dataset release; independent signed replay claims, not proof of computation.'}
    state['dataset_release']={'id':sha256(canonical_json(release)),'body':release,'height':height}
    state['dataset_releases'].append(state['dataset_release'])
    return {'dataset_release':state['dataset_release']['id']}
