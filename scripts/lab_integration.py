"""Small real end-to-end fixture; never presented as the language-model experiment."""
import argparse
from argparse import Namespace
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import numpy as np
import uvicorn
from dawl_node.common import write_json,sha256
from dawl_scratch.data import file_hash
from dawl_lab.data import prepare
from dawl_lab.run import create,train,replay,validate_lineage
from dawl_lab.verify import verify_data,verify_run
from dawl_lab.artifacts import publish,pull,verify_package,replicate,app

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();root=a.root
root.mkdir(parents=True,exist_ok=False);corpus=root/'corpus';(corpus/'texts').mkdir(parents=True)
documents=[]
for domain in ('prose','code'):
    for split in ('train','validation','test'):
        text=(f'{domain} {split} The small laboratory records each experiment. A model reads words and predicts the next word. '
              'Forskere undersøker språk og dokumenterer resultater. def add(a, b): return a + b\n')*3
        digest=sha256(text.encode());(corpus/'texts'/digest).write_text(text)
        documents.append({'id':domain+'/'+split,'group':domain+'/'+split,'domain':domain,'language':'en','split':split,'text_sha256':digest,'characters':len(text)})
write_json(corpus/'sources.json',{'documents':documents,'exclusions':[]})
write_json(corpus/'source-lock.json',{'sources_sha256':file_hash(corpus/'sources.json')})
write_json(corpus/'plan.json',{'seed':7,'context':16,'vocab_size':260,'tokenizer_characters_per_domain':10000,'mixture':{'prose':.5,'code':.5}})
dataset=root/'dataset';prepare(dataset,corpus)
data_result=verify_data(dataset,corpus,True)
args=Namespace(device='cpu',precision='fp32',attention='math',threads=2,width=32,heads=4,kv_heads=2,layers=2,hidden=64,
               seed=7,steps=3,batch_size=4,micro_batch=2,learning_rate=.001,checkpoint_every=1,compile=False,parent=None)
run=root/'run';create(run,dataset,args);train(run,dataset,'cpu',stop_step=1);train(run,dataset,'cpu',resume=True)
exact=replay(run,dataset,1,3,'cpu');assert exact['accepted']
verified=verify_run(run,dataset)
publication=publish(root/'provider',dataset,run,3)

class Server:
    def __init__(self,where):
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen(128)
        self.url='http://127.0.0.1:'+str(self.sock.getsockname()[1])
        self.server=uvicorn.Server(uvicorn.Config(app(where),log_level='error',access_log=False))
        self.thread=threading.Thread(target=self.server.run,kwargs={'sockets':[self.sock]},daemon=True)
    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:return self
            time.sleep(.05)
        raise RuntimeError('Provider did not start')
    def __exit__(self,*_):self.server.should_exit=True;self.thread.join(5);self.sock.close()

bundle=root/'consumer';partial=bundle/'run/checkpoints/0000003/state.safetensors.partial'
partial.parent.mkdir(parents=True);partial.write_bytes((run/'checkpoints/0000003/state.safetensors').read_bytes()[:12345])
with Server(root/'provider') as provider:
    transfer=pull(bundle,provider.url,publication['package_id'],publication['publisher'])
assert any(v['resumed_bytes']==12345 for v in transfer['transfers'])
package=verify_package(bundle,publication['publisher'])
assert not list(bundle.rglob('*.pem'))
replicate(root/'replica',bundle,publication['publisher'])
with Server(root/'replica') as provider:
    replica_transfer=pull(root/'second-consumer',provider.url,publication['package_id'],publication['publisher'])
verify_package(root/'second-consumer',publication['publisher'])
args.parent=bundle/'run';args.parent_step=3;args.expected_parent_key=(run/'operator.public.key').read_text().strip();args.steps=2
create(root/'branch',dataset,args);train(root/'branch',dataset,'cpu')
branch_replay=replay(root/'branch',dataset,0,2,'cpu');assert branch_replay['accepted']
assert (root/'branch/operator.public.key').read_text()!=(run/'operator.public.key').read_text()
verify_run(root/'branch',dataset)
# Publish a step-zero parent for two real Gloo ranks to execute the full training path.
subprocess.run([sys.executable,'-m','torch.distributed.run','--standalone','--nproc-per-node=2','-m','dawl_lab.ddp',
                '--parent',str(run),'--dataset',str(dataset),'--root',str(root/'distributed'),'--start','0','--end','3',
                '--expected-parent-key',args.expected_parent_key,'--device','cpu'],check=True)
ddp_result=verify_run(root/'distributed/0',dataset)
result={'schema':'hypertrix.lab-integration/1','accepted':True,'data':data_result,'training':verified,
        'replay_exact':exact['accepted'],'branch_replay_exact':branch_replay['accepted'],'package':package,
        'resumed_bytes':12345,'replica_received_bytes':replica_transfer['total_received_bytes'],'distributed':ddp_result,
        'scope':'Small synthetic fixture, HTTP transfer/resume/replica and real two-process CPU training. Separate from Seed III quality and multi-machine GPU tests.'}
write_json(root/'integration.json',result);print(json.dumps(result))
