"""DAWL consensus cluster: complete corpus replay and training/weight lineage foundation."""
import argparse
import asyncio
from pathlib import Path
import sys
import ipaddress

sys.path.insert(0,str(Path(__file__).resolve().parent/"src"))
from dawl_node.common import canonical_json, load_key, read_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("prepare","abci","provider","worker","status","publish","plan-training","inspect-loader"))
    parser.add_argument("--root",type=Path,default=Path("artifacts/cluster-v2"))
    parser.add_argument("--source",type=Path,default=Path("artifacts/platform-v1"))
    parser.add_argument("--weights",type=Path,default=Path("artifacts/platform-assets/model.safetensors"))
    parser.add_argument("--workers",type=int,default=4)
    parser.add_argument("--shard-chunks",type=int,default=64)
    parser.add_argument("--lease-blocks",type=int,default=60)
    parser.add_argument("--port",type=int,default=8900)
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--uds",type=Path,help="private Unix socket for the ABCI application")
    parser.add_argument("--allow-insecure-lab",action="store_true")
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--verify-weights",action="store_true")
    parser.add_argument("--peer",default="http://127.0.0.1:8930")
    args=parser.parse_args()
    if args.command=="prepare":
        from dawl_cluster.prepare import prepare
        result=prepare(args.root,args.source,args.weights,args.workers,args.shard_chunks,lease_blocks=args.lease_blocks)
    elif args.command in ("abci","provider"):
        import uvicorn
        if args.command=="abci":
            from dawl_cluster.application import app
            if not ipaddress.IPv4Address(args.host).is_loopback: parser.error("ABCI application must bind to container loopback")
        else:
            from dawl_cluster.provider import app
            if args.host!="127.0.0.1" and not args.allow_insecure_lab: parser.error("explicit lab bind required")
        if args.uds and args.command!='abci': parser.error('Unix socket is only supported for ABCI')
        uvicorn.run(app(args.root),host=args.host,port=args.port,uds=str(args.uds) if args.uds else None,access_log=False,limit_concurrency=32)
        return
    elif args.command=="worker":
        from dawl_cluster.worker import run
        asyncio.run(run(args.root,args.once));return
    elif args.command=="inspect-loader":
        from dawl_cluster.training import inspect
        result=inspect(args.root,args.peer,verify_weights=args.verify_weights)
    else:
        from dawl_cluster.client import Client
        from dawl_cluster.state import summary
        async def execute():
            client=Client(args.root,load_key(args.root/"key.private.pem") if args.command!="status" else None)
            try:
                if args.command=="status": return summary(await client.state())
                if args.command=="publish": return await client.command("publish_dataset",{})
                state=await client.state()
                if not state["dataset_release"]: raise ValueError("dataset is not yet released")
                return await client.command("plan_training",{"dataset_release":state["dataset_release"]["id"],
                    "parent_weight":client.plan["imported_weight"]["id"],"recipe":{"executor":"causal-lm-next-token/v1",
                    "sequence_length":128,"seed":20261013,"requires":"TRAINER_GPU","optimizer":"adamw","learning_rate":"0.0003"}})
            finally: await client.close()
        result=asyncio.run(execute())
    print(canonical_json(result).decode(),flush=True)


if __name__=="__main__": main()
