"""Export only public evidence and verified measurements from a completed pilot."""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(Path(__file__).resolve().parent))
from dawl_node.common import read_json,write_json,canonical_json,sha256
from verify_oracle_training import verify


def export(root, destination, pin):
    proof=verify(root,pin); report=read_json(root/'training-report.json')
    cert=read_json(root/'training-certificate.json'); state=cert['state']; stage=state['training_stages'][report['stage']]
    destination.mkdir(parents=True,exist_ok=False)
    for name in ('genesis.json','stage.json','manifest.json','training-certificate.json','application-history.json',
                 'training-transactions.json','churn.json','training-report.json','public-evidence.json'):
        shutil.copyfile(root/name,destination/name)
    shutil.copyfile(root/'certificate.json',destination/'source-certificate.json')
    shutil.copyfile(root/'report.json',destination/'source-report.json')
    write_json(destination/'verification.json',proof)
    receipts=[]; events={}; transfers={}
    for name in ('trainer-0','trainer-1','trainer-2','verifier-0','verifier-1'):
        output=destination/name;output.mkdir()
        for filename in ('events.jsonl','transfers.jsonl','dht-status.json'):
            path=root/name/filename
            if path.exists():shutil.copyfile(path,output/filename)
        events[name]=[json.loads(line) for line in (root/name/'events.jsonl').read_text().splitlines()]
        journal=[json.loads(line) for line in (root/name/'transfers.jsonl').read_text().splitlines()]
        transfers[name]={'objects':len(journal),'bytes':sum(row['artifact']['bytes'] for row in journal),
                         'source_peers':sorted({row['peer'] for row in journal})}
        for path in sorted((root/name/'public').glob('replay-*.json')):
            value=read_json(path);r=value['candidate']['round']
            cp=state['training_checkpoints'][stage['checkpoints'][r]]
            if value['candidate']!=cp['body'] or len(value['checks'])!=stage['body']['shards'] or not all(c['bit_exact'] is True for c in value['checks']):
                raise ValueError('numerical replay records differ from checkpoint')
            receipts.extend(value['checks']);shutil.copyfile(path,output/path.name)
        final=root/name/'public/final.json'
        if final.exists():shutil.copyfile(final,output/'final.json')
    if len(receipts)!=report['verifier_replays_required']:raise ValueError('missing observed numerical replay checks')
    with sqlite3.connect('file:'+str(root/'validator-3/application.sqlite')+'?mode=ro',uri=True) as db:
        outage_height=db.execute('SELECT height FROM state WHERE id=1').fetchone()[0]
    genesis=read_json(root/'genesis.json'); stopped=genesis['validators'][3]['address']
    commits=cert['signed_header']['commit']['signatures']
    signed=[s['validator_address'] for s in commits if s['block_id_flag']==2]
    if stopped in signed or len(signed)!=3 or outage_height>=state['height']:
        raise ValueError('certificate does not establish expected three-validator progress')
    availability=read_json(root.parent/'consumer/replica/availability.json')
    if availability['checkpoint']!=stage['head']:raise ValueError('cold consumer fetched wrong checkpoint')
    write_json(destination/'availability.json',availability)
    metrics={**report,'actual_bit_exact_replays':len(receipts),'transfers':transfers,
             'training_seconds':{name:sum(e['seconds'] for e in records if e['event']=='contributed') for name,records in events.items() if name.startswith('trainer')},
             'one_validator_offline':{'stopped_address':stopped,'last_committed_height':outage_height,'certified_height_after':state['height'],'signatures':len(signed)},
             'cold_consumer':availability}
    write_json(destination/'summary.json',metrics)
    total_updates=sum(e['event']=='contributed' for records in events.values() for e in records)
    if total_updates!=report['updates']:raise ValueError('trainer logs differ from certified update count')
    print(json.dumps(metrics,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--genesis-sha256',required=True);a=p.parse_args();export(a.root,a.output,a.genesis_sha256)
