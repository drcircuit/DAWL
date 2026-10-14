"""Allowlisted public evidence handoff; never archive runtime or credential trees."""
import hashlib
import json
from pathlib import Path
import re
import zipfile
from verify_hybrid_handoff import verify

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'artifacts/azure-hybrid-001'
OUT=ROOT/'output/Hypertrix_Yngvar_Handoff_2026-10-14.zip'


def main():
    items={}
    def add(path,name):
        assert path.is_file(),path
        assert name not in items and not any(s in name.lower() for s in ('.private.','credential','private-archives'))
        payload=path.read_bytes()
        if re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----',payload):raise ValueError('Private key in '+name)
        if path.suffix in ('.json','.jsonl'):
            for pattern in (rb'"(?:storageAccountKey|accountKey|clientSecret|accessToken)"\s*:\s*"[^"\s]{12,}',
                            rb'AccountKey=[A-Za-z0-9+/=]{20,}',rb'[?&]sig=[A-Za-z0-9%+/=]{20,}'):
                if re.search(pattern,payload,re.I):raise ValueError('Potential credential in '+name)
        items[name]=payload
    pdf='Hypertrix_Hybrid_Research_Update_2026-10-14.pdf'
    add(ROOT/'output/pdf'/pdf,pdf)
    architecture='Hypertrix_Architecture_2026-10-14.pdf'
    add(ROOT/'output/pdf'/architecture,architecture)
    for name in ('hybrid_research_update.md','hybrid_testnet.md','hypertrix_run_001.md','contribution_pilot_status.md',
                 'contributor_protocol.md','gpu_run_001.md','hypertrix_terminology.md','architecture_notes.md'):
        add(ROOT/'docs'/name,'docs/'+name)
    for filename in ('results/report.json','partition.json','seed-transfer.json','results/public-certified-state.json'):
        add(SOURCE/'evidence'/filename,'evidence/pooled/'+filename)
    for filename in ('results/report.json','results/audit-analysis.json','results/convergence.json','results/public-certified-state.json',
        'results/recovery-certificate.json','results/status.json','partition.json','node-faults.json','private-interface-test.json',
        'seed-transfer.json','fresh-identities.json','network-pins.json','role-images.json','topology.json'):
        add(SOURCE/'node-evidence'/filename,'evidence/per-node/'+filename)
    for path in sorted((SOURCE/'contribution-public-evidence').glob('*.json')):
        add(path,'evidence/contribution/'+path.name)
    for path in sorted((ROOT/'docs/results/hybrid-gpu-run-001').iterdir()):
        assert path.suffix in ('.json','.key')
        add(path,'evidence/gpu/'+path.name)
    for name in ('network-recovery-final.json','network-recovery-consoles.json','network-recovery-deadline.json',
                 'gpu-gateway-route.json'):
        add(SOURCE/name,'evidence/recovery/'+name)
    for path in sorted(SOURCE.glob('network-probe-result-*.json')):add(path,'evidence/recovery/'+path.name)
    for name in ('gpu_reconstruction_node.py','cuda_gather.py','gpu_operator.py','gpu_report.py',
        'peer_node.py','container_node.py','contributor_node.py','hybrid_topology.py','build_hybrid_report.py',
        'build_architecture_sheets.py','package_hybrid_report.py'):
        add(ROOT/'scripts'/name,'implementation/scripts/'+name)
    add(ROOT/'scripts/azure/enable_gpu_peer.py','implementation/scripts/azure/enable_gpu_peer.py')
    add(ROOT/'tests/test_gpu_reconstruction.py','implementation/tests/test_gpu_reconstruction.py')
    for name in ('Dockerfile.gpu','Dockerfile.nodes','consensus/go.mod','consensus/go.sum','consensus/main.go',
                 'deploy/azure/contribution-pool.bicep'):
        add(ROOT/name,'implementation/'+name)
    add(ROOT/'scripts/verify_hybrid_handoff.py','verify_handoff.py')
    items['README.txt']=('''HYPERTRIX - HANDOFF FOR YNGVAR - 14 OCTOBER 2026

Start with Hypertrix_Hybrid_Research_Update_2026-10-14.pdf.
The two landscape architecture sheets are embedded in the report and also
available separately as Hypertrix_Architecture_2026-10-14.pdf.
Editable prose is in docs/hybrid_research_update.md. Other docs retain context
and distinguish the historical runs from current architecture.
docs/architecture_notes.md expands the protocol map and proposed security tests.
Descriptive evidence references in the PDF link to its evidence guide.

The evidence is public metadata, certificates, signed claims and test results.
It contains no node private keys, cloud credentials, private archives, raw book
bodies, embedding table or trained model binaries. The GPU node is a diagnostic
observer, not an admitted consensus auditor. No model was trained.

Offline package validation (Python 3 with cryptography installed):
  python verify_handoff.py Hypertrix_Yngvar_Handoff_2026-10-14.zip

This checks all manifest hashes, the GPU report's Ed25519 signature, report/input
bindings, result counts and historical signed DHT advertisements. It does NOT
verify the CometBFT quorum signatures, current freshness or truth of claims.
The pilot's independent Go verifier checked certificates during execution.
MANIFEST.json provides integrity, not an external timestamp or independent trust.

Live reproduction requires the full DAWL repository and pinned public inputs.
The focused implementation files here are an inspection snapshot, not a turnkey
cluster distribution. Do not clone a live validator. Read docs/gpu_run_001.md
before using the WSL-specific scripts. Historic discovery records have expired;
their observation timestamps, rather than the present time, define the test.

PDF builders: implementation/scripts/build_hybrid_report.py and
implementation/scripts/build_architecture_sheets.py, run within the full
repository with reportlab, matplotlib and pypdf. Review rendered output after edits.
The GPU source files exactly match hashes inside the signed report.

Infrastructure lifecycle records remain in the historical evidence and operator
runbooks. This report focuses on architecture and experimental results.
''').encode()
    manifest={'schema':'hypertrix.public-handoff/1','files':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
                for name,data in sorted(items.items())}}
    items['MANIFEST.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(items.items()):z.writestr(name,data)
    result=verify(OUT)
    result.update(path=str(OUT),bytes=OUT.stat().st_size,sha256=hashlib.sha256(OUT.read_bytes()).hexdigest())
    (ROOT/'output/Hypertrix_Yngvar_Handoff_2026-10-14.verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
