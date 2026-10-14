"""Read-only host telemetry for the local adaptation lane, outside Oracle state."""
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime,timezone

PILOT=Path('/var/lib/dawl/finetune-economics-001')
OUTPUT=Path('/var/lib/dawl/oracle-training-003/console-telemetry/finetune.json')


def collect():
    ids=subprocess.check_output(['podman','ps','-aq','--filter','label=org.hypertrix.finetune=001'],text=True,timeout=15).split()
    containers=json.loads(subprocess.check_output(['podman','inspect',*ids],text=True,timeout=15)) if ids else []
    nodes=[]
    for c in containers:
        pilot=Path(c.get('Config',{}).get('Labels',{}).get('org.hypertrix.pilot',str(PILOT)))
        events=[json.loads(l) for l in (pilot/'events.jsonl').read_text().splitlines() if l][-20:] if (pilot/'events.jsonl').exists() else []
        verifier='replay' in c['Name'];failed=c['Name']=='hypertrix-finetune-001'
        node={'name':c['Name'],'container':c['Name'],'role':'local-replay' if verifier else 'local-adaptation',
              'id':c['Id'][:12],'image':c['Image'],'status':c['State']['Status'],'running':c['State']['Running'],
              'exit_code':c['State']['ExitCode'],'historical_attempt':failed,'stats':{},'events':[] if failed or verifier else events,
              'public':(pilot/'operator.public.key').read_text().strip(),'host':'Local WSL / RTX 5090 CUDA',
              'scope':'Separate local calibration branch. Not an Oracle-admitted training job or quorum-certified checkpoint.',
              'started_at':c['State']['StartedAt'],'finished_at':c['State']['FinishedAt'],'dht':None,'final':None,'replays':[]}
        if verifier and (pilot/'replay-verification.json').exists():
            node['replays']=[json.loads((pilot/'replay-verification.json').read_text())]
        nodes.append(node)
    return {'observed_at':datetime.now(timezone.utc).isoformat(),'nodes':nodes}


while True:
    try:
        value=collect();pending=OUTPUT.with_suffix('.pending');pending.write_text(json.dumps(value));os.replace(pending,OUTPUT)
    except Exception as e:print(type(e).__name__,flush=True)
    time.sleep(5)
