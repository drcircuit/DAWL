"""Pull-only worker. No arbitrary submitted code and no consensus validator key."""
import asyncio
from pathlib import Path
import time

import httpx
from dawl_node.common import load_key, read_json
from dawl_platform.workflow import open_node, implementation_digest
from dawl_platform.auditor import verify_book
from dawl_swarm.node import lineage_files
from dawl_swarm.transfer import Puller, Unavailable
from .client import Client
from .state import AUDIT


async def run(root, once=False):
    root=Path(root)
    client=Client(root,load_key(root/"key.private.pem"))
    ledger,objects=open_node(root)
    ledger.verify_history()
    if ledger.checkpoint()!=client.plan["lineage_checkpoint"]:
        raise ValueError("worker lineage differs from committed input checkpoint")
    implementation=implementation_digest()
    if implementation!=client.plan["implementation_sha256"]:
        raise ValueError("worker implementation differs from pinned recipe")
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
                lease=await client.command("claim",{"capability":AUDIT})
                if lease["job"] is None:
                    if once: return
                    await asyncio.sleep(3); continue
                spec=next(j["spec"] for j in client.plan["jobs"] if j["id"]==lease["job"])
                entry=ledger.get(spec["vectors_entry"])
                heartbeat=asyncio.create_task(renew(lease))
                started=time.perf_counter()
                report={"outcome":"error","checked_chunks":0,"tokens_checked":0,"dense_bytes":0,
                        "implementation_sha256":implementation,"execution_ms":0,"error":""}
                client.event("job_started",job=lease["job"],generation=lease["generation"])
                try:
                    for descriptor in lineage_files(ledger,entry).values():
                        if descriptor["sha256"] not in fetched:
                            await puller.fetch(descriptor,client.config["providers"])
                            fetched.add(descriptor["sha256"])
                    result=await asyncio.to_thread(verify_book,ledger,objects,None,entry,tables,spec["positions"])
                    report.update(outcome="pass",checked_chunks=result["checked_chunks"],
                                  tokens_checked=result["dense_bytes_reconstructed"]//(4*client.plan["embedding_dimension"]),
                                  dense_bytes=result["dense_bytes_reconstructed"])
                except Unavailable as error:
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
