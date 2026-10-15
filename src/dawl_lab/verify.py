"""Independent source/window reconstruction and signed checkpoint verification."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import numpy as np
from tokenizers import Tokenizer
from dawl_node.common import write_json,sha256,verify_signature
from dawl_scratch.data import file_hash
from .data import load
from .run import validate_lineage
from dawl_seed.progress import progress


def verify_data(dataset,corpus,full=False):
    bp,arrays=load(dataset);sources=json.loads((dataset/'sources.json').read_bytes())['documents']
    index=json.loads((dataset/'windows.json').read_bytes());rows={r['id']:r for r in index}
    if len(rows)!=len(index) or len(rows)!=len(sources):raise ValueError('Source/window identity mismatch')
    groups={};hashes={};counts=Counter();verified=0
    tokenizer=Tokenizer.from_file(str(dataset/'tokenizer.json'))
    progress_root=os.environ.get('HYPERTRIX_PROGRESS_ROOT')
    for i,d in enumerate(sources):
        for key,mapping in [(d['group'],groups),(d['text_sha256'],hashes)]:
            if key in mapping and mapping[key]!=d['split']:raise ValueError('Cross-split source leakage')
            mapping[key]=d['split']
        raw=(corpus/'texts'/d['text_sha256']).read_bytes()
        if sha256(raw)!=d['text_sha256']:raise ValueError('Source object hash differs')
        row=rows[d['id']]
        if any(row[k]!=d[k] for k in ('split','domain','group')):raise ValueError('Window metadata differs')
        counts[d['split']]+=row['target_tokens']
        if full or int(sha256(d['id'].encode())[:8],16)%100==0:
            tokens=tokenizer.encode(raw.decode('utf-8'),add_special_tokens=False).ids+[bp['eos_id']]
            expected=[]
            for at in range(0,len(tokens)-1,bp['context']):
                chunk=tokens[at:at+bp['context']+1];expected.append(chunk+[bp['pad_id']]*(bp['context']+1-len(chunk)))
            actual=arrays[d['split']][row['start_row']:row['end_row']]
            if not np.array_equal(np.asarray(expected,dtype='<u2'),actual):raise ValueError('Token reconstruction differs: '+d['id'])
            verified+=1
        if progress_root and i%250==0:
            progress(progress_root,'Verifying token windows',job='Seed III data verifier',role='tokenizer',status='running',completed=i+1,total=len(sources),unit='documents')
    if any(counts[s]!=bp['arrays'][s]['targets'] for s in counts):raise ValueError('Target totals differ')
    if progress_root:progress(progress_root,'Corpus verified',job='Seed III data verifier',role='tokenizer',status='complete',completed=len(sources),total=len(sources),unit='documents')
    return {'schema':'hypertrix.lab-data-verification/1','accepted':True,'blueprint_sha256':file_hash(dataset/'blueprint.json'),
        'source_objects_hashed':len(sources),'documents_retokenized':verified,'mode':'all-documents' if full else 'deterministic-one-percent-sample',
        'checks':['metadata/token-file hashes','source-object hashes','disjoint exact hashes and source families','source-to-window token reconstruction']}


def verify_run(root,dataset,expected_public=None):
    records=validate_lineage(root,expected_public);first=records[0]['payload']
    for name,key in [('config.json','config_sha256'),('recipe.json','recipe_sha256'),('implementation.json','implementation_sha256'),('schedule.json','schedule_manifest_sha256')]:
        if file_hash(root/name)!=first[key]:raise ValueError('Signed initialization differs')
    recipe=json.loads((root/'recipe.json').read_bytes())
    if recipe['blueprint_sha256']!=file_hash(dataset/'blueprint.json'):raise ValueError('Blueprint binding differs')
    load(dataset)
    for name,digest in json.loads((root/'implementation.json').read_bytes()).items():
        if file_hash(root/'implementation'/name)!=digest:raise ValueError('Frozen code differs')
    if file_hash(root/'schedule.npy')!=recipe['schedule_sha256']:raise ValueError('Schedule differs')
    checkpoints=0;replays=[];initialization=None
    if not recipe.get('world_size'):
        zero=next(r['payload'] for r in records if r['kind']=='checkpoint' and r['payload']['step']==0)
        if recipe.get('parent'):
            initialization=zero['state_sha256']==recipe['parent']['state_sha256']
        else:
            import torch
            from .model import Config,LanguageModel
            from .run import load_checkpoint
            torch.manual_seed(recipe['seed']);expected=LanguageModel(Config(**json.loads((root/'config.json').read_bytes())))
            actual,_,_=load_checkpoint(root,0)
            initialization=all(torch.equal(value,actual.state_dict()[name]) for name,value in expected.state_dict().items())
            del expected,actual
        if not initialization:raise ValueError('Starting state differs from declared initialization/parent')
    for row in records:
        body=row['payload']
        if row['kind']=='ddp-completion':
            if file_hash(root/body['path'])!=body['sha256'] or file_hash(root/'participants.json')!=body['participants_sha256']:raise ValueError('DDP completion binding differs')
            people=json.loads((root/'participants.json').read_bytes());receipts=json.loads((root/body['path']).read_bytes())
            if len(receipts)!=body['ranks'] or len(people)!=len(receipts):raise ValueError('Missing DDP rank')
            states=set()
            for rank,(person,receipt) in enumerate(zip(people,receipts)):
                who=verify_signature(person,person['body']['public_key']);result=verify_signature(receipt,who['public_key'])
                if who['rank']!=rank or result['rank']!=rank or result['end']!=body['end_step'] or result['participants_sha256']!=body['participants_sha256']:raise ValueError('DDP participant/result mismatch')
                states.add(result['final_model_digest'])
            if len(states)!=1:raise ValueError('DDP ranks disagree')
        if row['kind']=='checkpoint':
            path=root/'checkpoints'/f'{body["step"]:07d}'
            if file_hash(path/'checkpoint.json')!=body['descriptor_sha256'] or file_hash(path/'state.safetensors')!=body['state_sha256']:raise ValueError('Checkpoint artifact differs')
            checkpoints+=1
        if row['kind'] in ('replay','training-result'):
            if file_hash(root/body['path'])!=body['sha256']:raise ValueError('Signed evidence differs')
            evidence=json.loads((root/body['path']).read_bytes())
            resources=evidence.get('resources',{})
            if resources.get('samples_file'):
                name=resources['samples_file']
                if Path(name).name!=name or file_hash(root/name)!=resources['samples_sha256']:raise ValueError('Resource evidence differs')
            if row['kind']=='replay':replays.append(evidence)
    return {'schema':'hypertrix.lab-artifact-verification/1','accepted':True,'records':len(records),'checkpoint_files_hashed':checkpoints,
            'initialization_verified':initialization,
            'successful_replay_intervals':[[r['start_step'],r['end_step']] for r in replays if r['accepted']],
            'scope':'Hashes, signatures and bindings checked here; recorded numerical replay receipts checked, not re-executed.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['data','run']);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--root',type=Path);p.add_argument('--corpus',type=Path);p.add_argument('--full',action='store_true');p.add_argument('--expected-public-key');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=verify_data(a.dataset,a.corpus,a.full) if a.command=='data' else verify_run(a.root,a.dataset,a.expected_public_key)
    write_json(a.output,result);print(json.dumps(result))
