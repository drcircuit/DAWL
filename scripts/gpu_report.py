"""WSL: export only public GPU evidence; check live discovery and release continuity."""
import argparse
import json
from pathlib import Path
import shutil
from hybrid_operator import run, save


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('/var/lib/dawl/dawlh1013185918-nodes'))
    p.add_argument('--output',type=Path,default=Path('docs/results/hybrid-gpu-run-001'))
    a=p.parse_args();out=a.output;out.mkdir(parents=True,exist_ok=True)
    code="""import asyncio,json,time,sys
sys.path.insert(0,'/app/src')
from pathlib import Path
import httpx
from dawl_cluster.client import Client
from dawl_node.common import read_json,verify_signature,canonical_json,sha256
from dawl_swarm.dht import validate
async def main():
 root=Path('/node');report=read_json(root/'last-report.json');public=(root/'public.key').read_text().strip()
 result=verify_signature(report,public);assert result['accepted'] and not result['consensus_receipt']
 c=Client(root)
 try:cert=await c.certificate(c.config['rpcs'][0],result['certified_height']);state=c.accept_certificate(cert)
 finally:await c.close()
 assert state['dataset_release']['id']==result['dataset_release']
 assert public not in state['actors'] and public not in state['plan']['actors']
 assert result['cuda_launches']==result['chunks']==1807
 assert result['tokens']==2061330 and result['dense_bytes']==result['tokens']*768*4
 hosts=[f'127.70.0.{i}' for i in [10,11,12,13,20,21,22,23,30,*range(40,60)]]
 async with httpx.AsyncClient(timeout=25,trust_env=False) as h:
  async def check(host):
   r=await h.get('http://'+host+':8903/dht/roles/AUDITOR');r.raise_for_status()
   rows=[v for v in r.json()['records'] if v['body']['signer']==public]
   assert len(rows)==1;body=validate(rows[0],c.config['network_sha256'])
   assert body['endpoint']=='http://127.70.0.23:8903'
   return {'host':host,'record':rows[0],'checked_at_unix':int(time.time())}
  peers=await asyncio.gather(*(check(host) for host in hosts))
 (root/'public-certificate.json').write_bytes(canonical_json(cert))
 (root/'discovery-verified.json').write_bytes(canonical_json({'peers':peers,'verified':True,'count':len(peers)}))
 print(json.dumps({'release':result['dataset_release'],'receipt':report['id'],'public_key':public,'discovery_peers':len(peers),'height':state['height'],'cuda_launches':result['cuda_launches'],'seconds':result['seconds'],'cuda_kernel_ms':result['cuda_event_kernel_ms'],'cuda_roundtrip_seconds':result['cuda_roundtrip_seconds'],'numpy_reference_seconds':result['numpy_reference_seconds'],'received_bytes':result['received_bytes']}))
asyncio.run(main())
"""
    summary=json.loads(run('podman','exec',a.root.name+'-gpu-0','python','-c',code))
    for name in ('last-report.json','status.json','gpu-self-test.json','input-state.json','discovered-providers.json',
                 'public-certificate.json','discovery-verified.json','public.key','genesis.json'):
        source=a.root/'gpu-0'/name
        payload=source.read_bytes()
        if b'PRIVATE KEY' in payload:raise ValueError('unexpected private material')
        (out/name).write_bytes(payload)
    shutil.copyfile(a.root/'gpu-launched.json',out/'launched.json')
    save(out/'summary.json',summary)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
