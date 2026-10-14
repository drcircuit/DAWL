"""Fence and remove only the stalled ephemeral ACI group; preserve closed archives."""
import base64
import json
from pathlib import Path
import time
from control import REPO,TOOLS,az,save
from launch import command


def main():
    root=REPO/'artifacts/azure-hybrid-001';run=json.loads((root/'deployment.json').read_text())
    pool=run['prefix']+'-nodes';scope=['--subscription',run['subscription'],'-g','DAWL']
    value=az('container','show',*scope,'-n',pool)
    if value['tags']['pilot']!=run['prefix'] or value['ipAddress'].get('ip') or value['provisioningState']!='Creating':raise ValueError('Only the recorded stalled group may be recreated')
    def execute(code):
        expression="python -c exec(__import__('base64').b64decode('"+base64.b64encode(code.encode()).decode()+"'))"
        output=command(TOOLS,'-m','azure.cli','container','exec',*scope,'-n',pool,'--container-name','gateway','--exec-command',expression,'--only-show-errors').decode()
        return output
    result=execute("from pathlib import Path;Path('/exchange/stop').touch();print('CLOSE_REQUESTED')")
    if 'CLOSE_REQUESTED' not in result:raise RuntimeError(result[-1000:])
    for _ in range(20):
        output=execute("from pathlib import Path;import json;v=[json.loads(p.read_text()) for p in Path('/exchange/status').glob('*.json')];print('NODES_CLOSED' if len(v)==7 and all(n['phase']=='stopped' for n in v) else 'CLOSING')")
        if 'NODES_CLOSED' in output:break
        time.sleep(3)
    else:raise RuntimeError('All node state must close before recreation')
    for _ in range(20):
        closed=az('container','show',*scope,'-n',pool)
        nodes=[c for c in closed['containers'] if c['name'] not in ('gateway','console')]
        if len(nodes)==7 and all(c['instanceView']['currentState']['state']=='Terminated' for c in nodes):break
        time.sleep(3)
    else:raise RuntimeError('Archive writers have not all terminated')
    # Export a second, separately named checkpoint before removing ephemeral volumes.
    command(REPO/'.venv/Scripts/python.exe',REPO/'scripts/azure/node_lifecycle.py','export-closed-state','--subscription',run['subscription'])
    current=json.loads((root/'contribution-archives-verified.json').read_text())
    if len(current['archives'])!=7:raise ValueError('Incomplete fresh archive set')
    save(root/'pre-recreate-archives.json',current)
    # This stops the in-flight ARM workflow, not the resource group or unrelated resources.
    active=root/'active-contribution-deployment.json'
    deployment=json.loads(active.read_text())['name'] if active.exists() else run['prefix']+'-contributions'
    az('deployment','group','cancel',*scope,'-n',deployment)
    az('container','delete',*scope,'-n',pool,'--yes')
    if any(c['name']==pool for c in az('container','list',*scope)):raise ValueError('Old signer group has not been removed')
    save(root/'recreate-fence.json',dict(pool=pool,archives=current['archives'],old_group_removed=True,private_shares_preserved=True))
    print('Ephemeral group removed after all seven nodes closed and their fresh archives were exported.',flush=True)


if __name__=='__main__':main()
