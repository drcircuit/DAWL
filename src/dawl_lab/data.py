"""Training-only tokenizer, whole-document windows and token-balanced schedules."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import numpy as np
from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
from dawl_node.common import immutable_write, write_json, sha256
from dawl_scratch.data import file_hash
from dawl_seed.progress import progress


def prepare(root, corpus):
    root.mkdir(parents=True,exist_ok=True)
    if (root/'blueprint.json').exists():raise ValueError('Dataset is already frozen')
    lock=json.loads((corpus/'source-lock.json').read_bytes())
    if file_hash(corpus/'sources.json')!=lock['sources_sha256']:raise ValueError('Source manifest differs')
    plan=json.loads((corpus/'plan.json').read_bytes())
    sources=json.loads((corpus/'sources.json').read_bytes())['documents']
    cap=plan['tokenizer_characters_per_domain']; seed=plan['seed'];context=plan['context']
    tokenizer=Tokenizer(models.BPE())
    tokenizer.pre_tokenizer=pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder=decoders.ByteLevel()
    fit_counts=Counter()
    def fit_texts():
        # Hashed chunks sample throughout long books, rather than only introductions.
        # Before a tokenizer exists, use explicit character quotas proportional to
        # the requested mixture; token-level balance is enforced by schedule().
        candidates=[]
        caps={d:int(cap*len(plan['mixture'])*w) for d,w in plan['mixture'].items()}
        for d in sources:
            if d['split']=='train':
                for at in range(0,d['characters'],32768):
                    candidates.append((sha256((str(seed)+d['id']+'/'+str(at)).encode()),d,at))
        for _,d,at in sorted(candidates,key=lambda x:x[0]):
            if fit_counts[d['domain']]>=caps[d['domain']]:continue
            raw=(corpus/'texts'/d['text_sha256']).read_bytes()
            if sha256(raw)!=d['text_sha256']:raise ValueError('Source text differs')
            text=raw.decode('utf-8');take=min(32768,caps[d['domain']]-fit_counts[d['domain']],len(text)-at)
            if take>0:
                fit_counts[d['domain']]+=take
                yield text[at:at+take]
    progress(root,'Fitting tokenizer',job='Seed III tokenizer',role='tokenizer',status='running',completed=0,total=1,unit='tokenizer')
    tokenizer.train_from_iterator(fit_texts(),trainer=trainers.BpeTrainer(vocab_size=plan['vocab_size'],min_frequency=2,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),special_tokens=['<|bos|>','<|eos|>','<|pad|>'],show_progress=False))
    tokenizer.save(str(root/'tokenizer.json'));pad=tokenizer.token_to_id('<|pad|>');eos=tokenizer.token_to_id('<|eos|>')
    immutable_write(root/'sources.json',(corpus/'sources.json').read_bytes())
    immutable_write(root/'source-lock.json',(corpus/'source-lock.json').read_bytes())
    immutable_write(root/'corpus-plan.json',(corpus/'plan.json').read_bytes())
    rows=Counter();targets=Counter();domains=defaultdict(Counter);index=[]
    streams={s:(root/(s+'.bin')).open('wb') for s in ('train','validation','test')}
    try:
        for i,d in enumerate(sources):
            raw=(corpus/'texts'/d['text_sha256']).read_bytes()
            if sha256(raw)!=d['text_sha256']:raise ValueError('Source changed during tokenization')
            ids=tokenizer.encode(raw.decode('utf-8'),add_special_tokens=False).ids+[eos]
            split=d['split'];start=rows[split];count=0
            for at in range(0,len(ids)-1,context):
                part=ids[at:at+context+1];n=len(part)-1;count+=n
                streams[split].write(np.asarray(part+[pad]*(context+1-len(part)),dtype='<u2').tobytes())
                rows[split]+=1
            targets[split]+=count;domains[d['domain']][split]+=count
            index.append({'id':d['id'],'split':split,'domain':d['domain'],'language':d['language'],'group':d['group'],
                          'start_row':start,'end_row':rows[split],'target_tokens':count,'characters':d['characters']})
            if i%250==0:
                progress(root,'Tokenizing corpus',job='Seed III corpus',role='tokenizer',status='running',
                         completed=i+1,total=len(sources),unit='documents',target_tokens=sum(targets.values()))
    finally:
        for stream in streams.values():stream.close()
    arrays={s:{'file':s+'.bin','sha256':file_hash(root/(s+'.bin')),'windows':rows[s],'targets':targets[s]} for s in streams}
    evaluation={}
    for split in ('validation','test'):
        evaluation[split]={}
        for domain in plan['mixture']:
            candidates=[j for d in index if d['domain']==domain and d['split']==split for j in range(d['start_row'],d['end_row'])]
            # Fixed hashed sample: not selected after observing model outcomes.
            rng=np.random.default_rng(seed+int(sha256((split+domain).encode())[:8],16))
            evaluation[split][domain]=sorted(int(v) for v in rng.choice(candidates,min(32,len(candidates)),replace=False))
    write_json(root/'windows.json',index);write_json(root/'evaluation.json',evaluation)
    blueprint={'schema':'hypertrix.lab-blueprint/1','source_lock_sha256':file_hash(root/'source-lock.json'),
        'sources_sha256':file_hash(root/'sources.json'),'plan_sha256':file_hash(root/'corpus-plan.json'),
        'tokenizer_sha256':file_hash(root/'tokenizer.json'),'windows_sha256':file_hash(root/'windows.json'),
        'evaluation_sha256':file_hash(root/'evaluation.json'),'vocab_size':tokenizer.get_vocab_size(),
        'context':context,'pad_id':pad,'eos_id':eos,'arrays':arrays,'documents':len(sources),
        'domains':{d:dict(v) for d,v in domains.items()},'mixture':plan['mixture'],
        'tokenizer_fit_characters':dict(fit_counts),'tokenizer_fit':'Training-only hashed 32768-character chunks throughout documents; mixture-proportional character quotas; byte BPE.',
        'window_policy':'One document per window; 1024-token stride, one-token overlap for next-token labels; right padding excluded from loss.'}
    write_json(root/'blueprint.json',blueprint)
    progress(root,'Corpus ready',job='Seed III corpus',role='tokenizer',status='complete',completed=len(sources),total=len(sources),unit='documents',target_tokens=targets['train'])
    return blueprint


def load(root, verify=True):
    root=Path(root);bp=json.loads((root/'blueprint.json').read_bytes())
    if verify:
        for name,key in [('tokenizer.json','tokenizer_sha256'),('sources.json','sources_sha256'),('source-lock.json','source_lock_sha256'),
                         ('windows.json','windows_sha256'),('evaluation.json','evaluation_sha256'),('corpus-plan.json','plan_sha256')]:
            if file_hash(root/name)!=bp[key]:raise ValueError('Dataset commitment differs: '+name)
    arrays={}
    for split,desc in bp['arrays'].items():
        path=root/desc['file']
        if verify and file_hash(path)!=desc['sha256']:raise ValueError('Token array hash differs')
        arrays[split]=np.memmap(path,mode='r',dtype='<u2',shape=(desc['windows'],bp['context']+1))
    return bp,arrays


def schedule(root,dataset,batch_size,steps,seed):
    """Deficit scheduling by NONPADDING target tokens; immutable row IDs enable replay."""
    bp,arrays=load(dataset);index=json.loads((dataset/'windows.json').read_bytes())
    domains=sorted(bp['mixture']);weights=np.array([bp['mixture'][d] for d in domains])
    if not np.isclose(weights.sum(),1) or (weights<=0).any():raise ValueError('Invalid mixture')
    rng=np.random.default_rng(seed);pools={};positions=Counter()
    for domain in domains:
        ids=np.array([j for d in index if d['domain']==domain and d['split']=='train' for j in range(d['start_row'],d['end_row'])],dtype=np.int64)
        if not len(ids):raise ValueError('Empty domain')
        pools[domain]=rng.permutation(ids)
    # Lengths are computed in bounded chunks rather than materializing the corpus.
    lengths=np.empty(len(arrays['train']),dtype=np.int32)
    for at in range(0,len(lengths),2048):lengths[at:at+2048]=(arrays['train'][at:at+2048,1:]!=bp['pad_id']).sum(1)
    result=np.empty((steps,batch_size),dtype=np.int64);counts=np.zeros(len(domains),dtype=np.int64)
    unique=set()
    for position in range(steps*batch_size):
        # Select the domain furthest behind its proportional token budget.
        slot=int(np.argmax(weights*(int(counts.sum())+bp['context'])-counts));domain=domains[slot]
        at=positions[domain]
        if at==len(pools[domain]):pools[domain]=rng.permutation(pools[domain]);positions[domain]=at=0
        row=int(pools[domain][at]);positions[domain]+=1
        result.flat[position]=row;counts[slot]+=int(lengths[row]);unique.add(row)
    np.save(root/'schedule.npy',result,allow_pickle=False)
    stats={'schema':'hypertrix.lab-schedule/1','sha256':file_hash(root/'schedule.npy'),'steps':steps,'batch_size':batch_size,
           'seed':seed,'target_tokens':int(counts.sum()),'unique_windows':len(unique),'unique_target_tokens':int(lengths[list(unique)].sum()),
           'domain_target_tokens':dict(zip(domains,map(int,counts))), 'policy':'Deterministic token-deficit mixing; each domain permuted without replacement before cycling.'}
    write_json(root/'schedule.json',stats);return stats
