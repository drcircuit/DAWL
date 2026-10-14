"""Set the existing independent stop backstop to the approved 24-hour total window."""
import json
from control import REPO,az,save


def main():
    root=REPO/'artifacts/azure-hybrid-001';run=json.loads((root/'deployment.json').read_text())
    quote=json.loads((root/'contribution-estimate.json').read_text())
    if quote['hours_total']!=24 or quote['approved_max_eur']!=50 or quote['estimated_envelope_eur']>50:raise ValueError('Approved window or estimate differs')
    identity=f"/subscriptions/{run['subscription']}/resourceGroups/DAWL/providers/Microsoft.Logic/workflows/{run['prefix']}-nodes-deadline"
    url='https://management.azure.com'+identity+'?api-version=2019-05-01'
    old=az('rest','--method','get','--url',url)
    definition=old['properties']['definition']
    expected=f"https://management.azure.com/subscriptions/{run['subscription']}/resourceGroups/DAWL/providers/Microsoft.ContainerInstance/containerGroups/{run['prefix']}-nodes/stop?api-version=2023-05-01"
    if definition['actions']['expired']['actions']['stop']['inputs']['uri'].lower()!=expected.lower():raise ValueError('Backstop targets another resource')
    definition['actions']['expired']['expression']['greaterOrEquals'][1]=quote['expires_at']
    body=dict(location=old['location'],identity={'type':'SystemAssigned'},tags={**old.get('tags',{}),'expiresAt':quote['expires_at']},
        properties=dict(state='Enabled',definition=definition,parameters=old['properties'].get('parameters',{})))
    save(root/'deadline-update.json',body)
    updated=az('rest','--method','put','--url',url,'--body','@'+str(root/'deadline-update.json'))
    if updated['identity']['principalId']!=old['identity']['principalId']:raise ValueError('Stop identity changed')
    save(root/'contribution-deadline.json',dict(id=identity,principal=updated['identity']['principalId'],expires_at=quote['expires_at'],state=updated['properties']['state']))
    print('Independent stop deadline extended to '+quote['expires_at'],flush=True)


if __name__=='__main__':main()
