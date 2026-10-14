"""Pull-only worker. No arbitrary submitted code and no consensus validator key."""
import asyncio
from pathlib import Path
import time
import os

import httpx
from dawl_node.common import load_key, read_json
from dawl_platform.workflow import open_node, implementation_digest
from dawl_platform.auditor import verify_book
from dawl_swarm.node import lineage_files
from dawl_swarm.transfer import Puller, Unavailable
from .client import Client
from .state import AUDIT, complete
from . import contributions
from .contribution_artifacts import import_manifest, catalog as build_catalog
from dawl_swarm.dht import validate as validate_peer


async def run(root, once=False):
    root=Path(root)
    if 'supported_profiles' in read_json(root/'cluster.json'):
        from .profile_worker import run as run_profiles
        return await run_profiles(root, once)
    client=Client(root,load_key(root/"key.private.pem"))
    ledger,objects=open_node(root)
    ledger.verify_history()
    if ledger.checkpoint()!=client.plan["lineage_checkpoint"]:
        raise ValueError("worker lineage differs from committed input checkpoint")
    implementation=implementation_digest()
    # The immutable genesis recipe remains valid for old jobs; an explicit committed
    # upgrade authorizes this executable for new contribution jobs.
    tables={}; fetched=set()
    transfer=httpx.AsyncClient(timeout=5,trust_env=False,follow_redirects=False)
    puller=Puller(transfer,objects,client,client.config["providers"])
    async def renew(lease):
        while True:
            await asyncio.sleep(10)
            try:
                await client.command("renew",{"job":lease["job"],"generation":lease["generation"]})
            except (ValueError,OSError) as error:
                client.event("renewal_failed",error=str(error)[:200])
    try:
        while True:
            try:
                observed=await client.state()
                active=observed.get('protocol_upgrade',{}).get('implementation_sha256',client.plan['implementation_sha256'])
                if implementation!=active:
                    client.event('waiting_for_protocol_activation',implementation=implementation)
                    await asyncio.sleep(5);continue
                eligible=any(client.public in j["leases"] or (
                    not complete(observed,j) and client.public not in j["attestations"]
                    and len(j["attestations"])+len(j["leases"])<observed["plan"]["replicas"]
                    and not any(f["outcome"]=="invalid" for f in j["failures"]))
                    for j in observed["jobs"].values())
                if not eligible:
                    if once: return
                    # Idle workers read committed state without creating empty claim transactions.
                    await asyncio.sleep(5); continue
                lease=await client.command("claim",{"capability":AUDIT})
                if lease["job"] is None:
                    if once: return
                    await asyncio.sleep(3); continue
                observed=await client.state();job=observed['jobs'][lease['job']];spec=job['spec']
                heartbeat=asyncio.create_task(renew(lease))
                started=time.perf_counter()
                report={"outcome":"error","checked_chunks":0,"tokens_checked":0,"dense_bytes":0,
                        "implementation_sha256":implementation,"execution_ms":0,"error":""}
                client.event("job_started",job=lease["job"],generation=lease["generation"])
                try:
                    source_ledger=ledger;providers=client.config['providers'];active_puller=puller
                    if job.get('contribution'):
                        contribution=observed['contributions'][job['contribution']]
                        url='http://'+os.environ['DAWL_HOST']+':8903/dht/roles/PROVIDER'
                        response=await transfer.get(url);response.raise_for_status();providers=[]
                        for record in response.json()['records']:
                            peer=validate_peer(record,client.config['network_sha256'])
                            if peer['signer']==contribution['provider'] and peer['provider']==observed['providers'][peer['signer']]['endpoint']:
                                providers.append(peer['provider'])
                                client.event('provider_discovered',peer_id=peer['peer_id'],provider=peer['signer'],endpoint=peer['provider'])
                        if not providers:raise Unavailable('No live DHT record for the committed contributor')
                        active_puller=Puller(transfer,objects,client,providers)
                        await active_puller.fetch(contribution['manifest'],providers)
                        source_ledger=import_manifest(root/'contributions'/job['contribution'],objects.read_json(contribution['manifest']),contribution,ledger.trust['anchors'])
                        # Rebuild the entire bounded batch catalog from fetched signed artifacts;
                        # this also binds training rows, metadata and counts to replay inputs.
                        for descriptor in lineage_files(source_ledger).values():
                            if descriptor['sha256'] not in fetched:
                                await active_puller.fetch(descriptor,providers);fetched.add(descriptor['sha256'])
                        if await asyncio.to_thread(build_catalog,source_ledger,objects)!=contribution['catalog']:raise ValueError('catalog does not reproduce signed input artifacts')
                    entry=source_ledger.get(spec['vectors_entry'])
                    for descriptor in lineage_files(source_ledger,entry).values():
                        if descriptor["sha256"] not in fetched:
                            await active_puller.fetch(descriptor,providers)
                            fetched.add(descriptor["sha256"])
                    result=await asyncio.to_thread(verify_book,source_ledger,objects,None,entry,tables,spec["positions"])
                    report.update(outcome="pass",checked_chunks=result["checked_chunks"],
                                  tokens_checked=result["dense_bytes_reconstructed"]//(4*client.plan["embedding_dimension"]),
                                  dense_bytes=result["dense_bytes_reconstructed"])
                except Unavailable as error:
                    report.update(outcome="unavailable",error=str(error)[:512])
                except httpx.HTTPError as error:
                    report.update(outcome="unavailable",error=str(error)[:512])
                except ValueError as error:
                    report.update(outcome="invalid",error=str(error)[:512])
                except (OSError,RuntimeError,KeyError,TypeError) as error:
                    report.update(outcome="error",error=str(error)[:512])
                finally:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat,return_exceptions=True)
                report["execution_ms"]=int((time.perf_counter()-started)*1000)
                result=await client.command("result",{"job":lease["job"],"generation":lease["generation"],"report":report})
                client.event("job_finished",job=lease["job"],report=report,result=result)
                print(f"{client.public[:12]} {lease['job'][:12]} {report['outcome']} {report['checked_chunks']} chunks",flush=True)
                if report["outcome"] in ("unavailable","error"): await asyncio.sleep(5)
            except (OSError,ValueError,KeyError) as error:
                client.event("worker_retry",error=str(error)[:300])
                print(f"Worker retry: {error}",flush=True)
                await asyncio.sleep(3)
    finally:
        await transfer.aclose()
        await client.close()
