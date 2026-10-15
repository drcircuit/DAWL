"""Consolidate completed lab evidence without converting signatures into quality claims."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
from dawl_node.common import verify_signature,write_json
from dawl_scratch.data import file_hash

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();root=a.root
def read(name):return json.loads((root/name).read_bytes())
summary=read('run/summary.json');old=read('quality-seed2.json');new=read('quality-seed3.json')
if old['prompts_sha256']!=new['prompts_sha256'] or old['snippets']!=new['snippets']:raise ValueError('Quality comparison inputs differ')
checks=read('public-package-verification.json')
if not checks['accepted']:raise ValueError('Public package verification failed')
receipts=list((root/'public-validation/audits').glob('*/result/receipt.json'))
if len(receipts)!=1:raise ValueError('Expected exactly one public replay receipt')
envelope=json.loads(receipts[0].read_bytes());receipt=verify_signature(envelope,envelope['body']['verifier'])
if not receipt['accepted'] or not receipt['optimizer_exact'] or receipt['end_step']!=summary['selected_step']:raise ValueError('Public replay did not accept the selected checkpoint')
comparison={d:{'seed_ii_bits_per_byte':old['likelihood'][d]['bits_per_utf8_byte'],
               'seed_iii_bits_per_byte':new['likelihood'][d]['bits_per_utf8_byte']} for d in new['likelihood']}
names=['data-verification.json','run/summary.json','quality-seed2.json','quality-seed3.json','public-publication.json',
       'public-transfer.json','public-package-verification.json','artifact-verification.json']
evidence=[{'path':name,'sha256':file_hash(root/name)} for name in names]
evidence.append({'path':receipts[0].relative_to(root).as_posix(),'sha256':file_hash(receipts[0])})
result={'schema':'hypertrix.lab-finalization/1','created_at':datetime.now(timezone.utc).isoformat(),'accepted':True,
        'selected_step':summary['selected_step'],'model_state_sha256':checks['state_sha256'],
        'public_package':read('public-publication.json'),'public_replay':{k:receipt[k] for k in
          ('verifier','model_operator','start_step','end_step','tensor_count','exact_tensors','optimizer_exact','calendar_elapsed_seconds','active_elapsed_seconds','resources')},
        'comparison':comparison,'training_summary':summary,'evidence':evidence,
        'scope':'Completed local training and separate-key public-package replay. Exact replay covers only the stated interval and recorded hardware cohort. Quality comparison uses small fixed held-out samples; it is not a validated capability benchmark. No multi-machine GPU result is implied.'}
write_json(root/'public-finalization.json',result)
lines=['# Seed III completed run',f'\nGenerated {result["created_at"]}.',f'\nSelected checkpoint: **{summary["selected_step"]}**.',
       f'\nPublic package: `{result["public_package"]["package_id"]}`.',
       f'\nSeparate verifier reproduced updates {receipt["start_step"]}–{receipt["end_step"]}: '
       f'{receipt["exact_tensors"]}/{receipt["tensor_count"]} tensors and AdamW state matched exactly.',
       '\n| Domain | Seed II bits/byte | Seed III bits/byte |','|---|---:|---:|']
for domain,row in comparison.items():lines.append(f'| {domain} | {row["seed_ii_bits_per_byte"]:.3f} | {row["seed_iii_bits_per_byte"]:.3f} |')
lines+=['\nLower bits/byte means better likelihood on these specific raw-text samples. Inspect the retained continuations for context and repetition.',
        '\n'+result['scope'],'\nFull resource measurements, artifact hashes and sample outputs are in `public-finalization.json` and its referenced evidence.']
(root/'completed-run.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'accepted':True,'selected_step':summary['selected_step'],'report':str(root/'completed-run.md')}))
