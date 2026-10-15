"""Common raw-text likelihood units and frozen continuation prompts for Seed II/III."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import time
import torch
from tokenizers import Tokenizer
from dawl_node.common import sha256,write_json
from dawl_scratch.data import file_hash
from dawl_lab.run import configure,load_checkpoint
from dawl_seed.run import load_checkpoint as load_seed

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--dataset',type=Path,required=True)
p.add_argument('--corpus',type=Path,required=True);p.add_argument('--step',type=int,required=True);p.add_argument('--legacy',action='store_true')
p.add_argument('--prompts',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--evaluation-sources',type=Path,required=True);a=p.parse_args()
configure('cpu','fp32','math',4);model,_,desc=(load_seed if a.legacy else load_checkpoint)(a.root,a.step,'cpu');model.eval()
tokenizer=Tokenizer.from_file(str(a.dataset/'tokenizer.json'));bp=json.loads((a.dataset/'blueprint.json').read_bytes())
if file_hash(a.dataset/'tokenizer.json')!=bp['tokenizer_sha256']:raise ValueError('Tokenizer identity differs')
rows=json.loads(a.evaluation_sources.read_bytes())['documents'];metrics={};snippets=[]
# Test-set documents are selected by a fixed hash before model outcomes are read.
with torch.inference_mode():
    for domain in sorted({r['domain'] for r in rows}):
        candidates=sorted((r for r in rows if r['domain']==domain and r['split']=='test'),key=lambda r:sha256(('quality-v1/'+r['id']).encode()))[:16]
        total=0.;nbytes=0;ntokens=0
        for row in candidates:
            raw=(a.corpus/'texts'/row['text_sha256']).read_bytes()
            if sha256(raw)!=row['text_sha256']:raise ValueError('Evaluation source differs')
            # Identical raw Unicode prefix for every tokenizer, no special-token text.
            text=raw.decode('utf-8')[:600];ids=tokenizer.encode(text,add_special_tokens=False).ids
            sequence=[tokenizer.token_to_id('<|bos|>')]+ids
            for at in range(0,len(sequence)-1,model.config.context):
                part=sequence[at:at+model.config.context+1]
                x=torch.tensor([part[:-1]]);y=torch.tensor([part[1:]])
                logits=model(x)[0];loss=torch.nn.functional.cross_entropy(logits.reshape(-1,logits.shape[-1]),y.reshape(-1),reduction='sum')
                total+=float(loss);ntokens+=len(part)-1
            nbytes+=len(text.encode());snippets.append({'id':row['id'],'text_sha256':sha256(text.encode()),'bytes':len(text.encode()),'domain':domain})
        metrics[domain]={'bits_per_utf8_byte':total/(math.log(2)*nbytes),'negative_log_likelihood':total,'bytes':nbytes,'tokens':ntokens,'documents':len(candidates)}
    outputs=[]
    for prompt in json.loads(a.prompts.read_bytes())['prompts']:
        for temperature in (0.,.7):
            ids=tokenizer.encode(prompt['text'],add_special_tokens=False).ids;generated=[];rng=torch.Generator().manual_seed(42);began=time.monotonic()
            for _ in range(256):
                logits=model(torch.tensor([ids[-model.config.context:]]))[0][0,-1].float()
                logits[bp['pad_id']]=float('-inf');logits[tokenizer.token_to_id('<|bos|>')]=float('-inf')
                for token in set(generated[-64:]):logits[token]=logits[token]/1.1 if logits[token]>=0 else logits[token]*1.1
                if temperature==0:chosen=int(logits.argmax())
                else:
                    scores,indices=torch.sort(logits/temperature,descending=True);prob=scores.softmax(-1)
                    prob[prob.cumsum(-1)-prob>=.9]=0;prob/=prob.sum();chosen=int(indices[torch.multinomial(prob,1,generator=rng)])
                if chosen==bp['eos_id']:break
                ids.append(chosen);generated.append(chosen)
                if len(tokenizer.decode(generated))>=384:break
            text=tokenizer.decode(generated);words=text.split();grams=[tuple(words[i:i+4]) for i in range(max(0,len(words)-3))]
            outputs.append({**prompt,'completion':text,'temperature':temperature,'seed':42,'top_p':.9,'repetition_penalty':1.1,
                            'tokens':len(generated),'elapsed_seconds':time.monotonic()-began,
                            'repeated_word_4gram_fraction':1-len(set(grams))/len(grams) if grams else 0})
            print(json.dumps({'prompt':prompt['id'],'temperature':temperature,'characters':len(text)}),flush=True)
result={'schema':'hypertrix.lab-quality/1','model':'Seed II' if a.legacy else 'Seed III','step':a.step,
        'state_sha256':desc['state_sha256'],'prompts_sha256':file_hash(a.prompts),'evaluation_sources_sha256':file_hash(a.evaluation_sources),
        'likelihood':metrics,'snippets':snippets,'continuations':outputs,
        'scope':'Same held-out raw 600-character prefixes, scored with BOS and tokenizer-specific context chunks; bits per UTF-8 byte. Qualitative prompts are not a validated capability benchmark. Generated code is never executed.'}
write_json(a.output,result)
