"""Generate bounded AKS workload manifests. No Azure or Kubernetes mutations."""
import argparse
import json
from pathlib import Path
import re

NS = 'dawl'


def members(workers):
    if workers not in (4, 20, 40):
        raise ValueError('supported pilot sizes are 4, 20 and 40 workers')
    return ([dict(name=f'validator-{i}', role='validator', host=f'10.240.1.{10+i}') for i in range(4)] +
            [dict(name=f'provider-{i}', role='provider', host=f'10.240.1.{20+i}') for i in range(2)] +
            [dict(name='publisher', role='publisher', host='10.240.1.30')] +
            [dict(name=f'worker-{i}', role='worker', host=f'10.240.1.{40+i}') for i in range(workers)])


def manifests(workers, image):
    if not re.fullmatch(r'[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('registry manifest digest required')
    nodes = members(workers)
    items = [dict(apiVersion='v1', kind='Namespace', metadata=dict(name=NS, labels={
        'pod-security.kubernetes.io/enforce': 'restricted'})),
        dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name='bootstrap', namespace=NS), data={}),
        dict(apiVersion='storage.k8s.io/v1', kind='StorageClass', metadata=dict(name='dawl-disk'),
             provisioner='disk.csi.azure.com', parameters={'skuName': 'StandardSSD_LRS'},
             reclaimPolicy='Retain', volumeBindingMode='WaitForFirstConsumer', allowVolumeExpansion=True)]
    policy = dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy', metadata=dict(name='private-cluster', namespace=NS),
                  spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[dict(
                      **{'from': [dict(podSelector={})]}, ports=[dict(protocol='TCP', port=p) for p in (26656, 26657, 8900)])],
                    egress=[dict(to=[dict(podSelector={})], ports=[dict(protocol='TCP', port=p) for p in (26656, 26657, 8900)]),
                            dict(to=[dict(namespaceSelector=dict(matchLabels={'kubernetes.io/metadata.name': 'kube-system'}))],
                                 ports=[dict(protocol=p, port=53) for p in ('TCP', 'UDP')])]))
    items.append(policy)
    for member in nodes:
        name, role, host = (member[k] for k in ('name', 'role', 'host'))
        labels = {'app': 'dawl', 'dawl-name': name, 'dawl-role': role}
        ports = (26656, 26657) if role == 'validator' else (8900,) if role == 'provider' else (9999,)
        items.append(dict(apiVersion='v1', kind='Service', metadata=dict(name=name, namespace=NS),
                          spec=dict(type='ClusterIP', clusterIP=host, selector={'dawl-name': name},
                                    ports=[dict(name=f'p{p}', port=p, targetPort=p) for p in ports])))
        resources = dict(requests=dict(cpu='2' if role == 'worker' else '500m', memory='1Gi'),
                         limits=dict(cpu='2', memory='2Gi'))
        pod = dict(automountServiceAccountToken=False, terminationGracePeriodSeconds=60,
                   securityContext=dict(runAsUser=1000, runAsGroup=1000, runAsNonRoot=True, fsGroup=1000,
                                        seccompProfile=dict(type='RuntimeDefault')),
                   nodeSelector={'dawl-pool': 'audit' if role == 'worker' else 'system'},
                   containers=[dict(name='node', image=image, imagePullPolicy='IfNotPresent',
                     command=['python', '/app/scripts/kubernetes_node.py'],
                     env=[dict(name='DAWL_'+key.upper(), value=value) for key, value in member.items()],
                     resources=resources, securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True,
                                                               capabilities=dict(drop=['ALL'])),
                     volumeMounts=[dict(name='state', mountPath='/data'), dict(name='bootstrap', mountPath='/bootstrap', readOnly=True),
                                   dict(name='tmp', mountPath='/tmp')])],
                   volumes=[dict(name='bootstrap', configMap=dict(name='bootstrap')),
                            dict(name='tmp', emptyDir=dict(medium='Memory', sizeLimit='256Mi'))])
        if role == 'worker':
            pod['tolerations'] = [dict(key='dawl-audit', operator='Equal', value='true', effect='NoSchedule')]
        if role in ('validator', 'provider'):
            pod['containers'][0]['readinessProbe'] = dict(tcpSocket=dict(port=26657 if role == 'validator' else 8900),
                                                         periodSeconds=5, failureThreshold=3)
        if role in ('validator', 'provider'):
            pod['affinity'] = dict(podAntiAffinity=dict(requiredDuringSchedulingIgnoredDuringExecution=[
                dict(labelSelector=dict(matchLabels={'dawl-role': role}), topologyKey='kubernetes.io/hostname')]))
        items.append(dict(apiVersion='apps/v1', kind='StatefulSet', metadata=dict(name=name, namespace=NS), spec=dict(
            serviceName=name, replicas=1, updateStrategy=dict(type='OnDelete'), selector=dict(matchLabels={'dawl-name': name}),
            template=dict(metadata=dict(labels=labels), spec=pod), volumeClaimTemplates=[dict(metadata=dict(name='state'), spec=dict(
                accessModes=['ReadWriteOnce'], storageClassName='dawl-disk', resources=dict(requests=dict(storage='8Gi'))))])))
    items.append(dict(apiVersion='policy/v1', kind='PodDisruptionBudget', metadata=dict(name='validator-quorum', namespace=NS),
                      spec=dict(minAvailable=3, selector=dict(matchLabels={'dawl-role': 'validator'}))))
    return dict(apiVersion='v1', kind='List', items=items)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=20)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifests(args.workers, args.image), indent=2)+'\n', encoding='utf-8')
