"""Create a new admitted chain from the pinned corpus without modifying its ledger."""
import base64
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import secrets
import shutil

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from dawl_node.common import canonical_json, keygen, read_json, sha256, write_json
from dawl_platform.workflow import open_node, implementation_digest
from dawl_platform.artifacts import Objects
from dawl_swarm.node import lineage_files
from .state import initial, AUDIT, PUBLISH


def comet_identity():
    key=Ed25519PrivateKey.generate()
    public=key.public_key().public_bytes_raw()
    seed=key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())
    return {"address":sha256(public)[:40].upper(),
            "pub_key":{"type":"tendermint/PubKeyEd25519","value":base64.b64encode(public).decode()},
            "priv_key":{"type":"tendermint/PrivKeyEd25519","value":base64.b64encode(seed+public).decode()}}


def prepare(root, source, weights, workers=4, shard_chunks=64, replicas=2, lease_blocks=60):
    root,source,weights=Path(root),Path(source),Path(weights)
    if root.exists(): raise ValueError("cluster root already exists; refusing to replace chain or identities")
    if not 2<=workers<=16 or not 1<=shard_chunks<=256: raise ValueError("invalid cluster size")
    original,objects=open_node(source,read_only=True)
    original.verify_history()
    entries=list(original.entries())
    vectors=sorted((e for e in entries if e["body"]["kind"]=="vectors"),key=lambda e:e["body"]["payload"]["book_id"])
    config=read_json(source/"config.json")
    if {e["body"]["payload"]["book_id"] for e in vectors}!={j["book_id"] for j in config["jobs"]}:
        raise ValueError("expected exactly one vector lineage for every locked book")
    if len(vectors)!=len(config["jobs"]): raise ValueError("ambiguous vector lineages")
    root.mkdir(parents=True)
    published=Objects(root/"published")
    files=lineage_files(original)
    for digest,desc in files.items():
        # Verify every source object before seeding the fresh independent provider copies.
        objects.read(desc)
        shutil.copyfile(objects.root/digest,published.root/digest)
    def obj(value):
        desc=published.json(value); files[desc["sha256"]]=desc; return desc
    embedding=original.get(config["table_entry"])["body"]["payload"]["descriptor"]
    whole=hashlib.sha256(); blocks=[]; size=0
    with weights.open("rb") as stream:
        while payload:=stream.read(16*1024*1024):
            whole.update(payload);size+=len(payload)
            desc=published.put(payload);files[desc["sha256"]]=desc;blocks.append(desc)
    if whole.hexdigest()!=embedding["weights_sha256"] or size!=embedding["weights_bytes"]:
        raise ValueError("parent model checkpoint differs from the existing embedding lineage")
    weight_manifest={"schema":"dawl.sharded-weight-artifact/1","format":"safetensors",
                     "sha256":whole.hexdigest(),"bytes":size,"block_bytes":16*1024*1024,"blocks":blocks}
    weight={"schema":"dawl.weight-record/1","kind":"import","artifact":obj(weight_manifest),
            "weights_sha256":whole.hexdigest(),"bytes":size,"upstream":embedding["upstream"],
            "revision":embedding["revision"],"tokenizer_sha256":embedding["tokenizer_sha256"],
            "parents":[],"training_history_verified":False,
            "boundary":"Imported pretrained weights; DAWL has not observed upstream training."}
    imported={"id":sha256(canonical_json(weight)),"body":weight}
    jobs=[];books=[];records=[]
    ordered=sorted((v["body"]["payload"]["book_id"] for v in vectors),key=lambda x:sha256(f"dawl-split-v1:{x}".encode()))
    splits={b:("test" if i==0 else "validation" if i==1 else "train") for i,b in enumerate(ordered)}
    for vector in vectors:
        vp=vector["body"]["payload"]
        token=original.get(vp["tokens_entry"])["body"]["payload"]
        index=objects.read_json(vp["files"]["index"])
        books.append({"book_id":vp["book_id"],"vectors_entry":vector["id"],"tokens_entry":vp["tokens_entry"],
                      "chunks":vp["chunks"],"tokens":vp["tokens"],"split":splits[vp["book_id"]]})
        for start in range(0,len(index),shard_chunks):
            chosen=index[start:start+shard_chunks]
            spec={"kind":"full-shard-replay/v1","vectors_entry":vector["id"],
                  "book_id":vp["book_id"],"positions":[r["source_index"] for r in chosen],
                  "tokens":sum(r["vector_bytes"] for r in chosen)//(4*embedding["shape"][1]),
                  "dense_bytes":sum(r["vector_bytes"] for r in chosen)}
            jobs.append({"id":sha256(canonical_json(spec)),"spec":spec})
        manifest=objects.read_json(token["files"]["manifest.json"])["body"]
        for shard in manifest["shards"]:
            rows=objects.read_json(token["files"][shard["name"]+".records.json"])
            for record in rows:
                records.append({"book_id":vp["book_id"],"source_index":record["source_index"],
                                "split":splits[vp["book_id"]],"file":token["files"][shard["name"]+".u32le"],
                                "offset":record["token_offset"],"count":record["token_count"],
                                "tokens_sha256":index[record["source_index"]]["tokens_sha256"],
                                "tokens_entry":vp["tokens_entry"]})
    training_manifest={"schema":"dawl.training-input/1","dtype":"uint32-le","tokenizer_sha256":embedding["tokenizer_sha256"],
                       "vocab_size":embedding["shape"][0],"records":records,"split_contract":"whole-book-hash-order/v1",
                       "sequence_contract":"within-source-chunk; next-token targets; pad with masked EOS; never cross split or chunk",
                       "tokens":sum(b["tokens"] for b in books),"chunks":len(records)}
    actors={}
    for name,hats in [("publisher",[PUBLISH])]+[(f"worker-{i}",[AUDIT]) for i in range(workers)]:
        public=keygen(root/name/"key.private.pem",root/name/"public.key")
        actors[public]={"name":name,"hats":hats}
    plan={"schema":"dawl.cluster-plan/1","chain_id":"dawl-"+secrets.token_hex(8),
          "lineage_checkpoint":original.checkpoint(),"actors":actors,"replicas":replicas,"lease_blocks":lease_blocks,
          "books":books,"jobs":jobs,"training_manifest":obj(training_manifest),"imported_weight":imported,
          "embedding_dimension":embedding["shape"][1],"implementation_sha256":implementation_digest()}
    initial(plan)
    write_json(root/"plan.json",plan)
    write_json(root/"artifact-index.json",files)
    validator_keys=[comet_identity() for _ in range(4)]
    node_keys=[comet_identity() for _ in range(4)]
    genesis={"genesis_time":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"chain_id":plan["chain_id"],
             "initial_height":"1","consensus_params":{"block":{"max_bytes":"1048576","max_gas":"-1"},
             "evidence":{"max_age_num_blocks":"100000","max_age_duration":"172800000000000","max_bytes":"1048576"},
             "validator":{"pub_key_types":["ed25519"]},"version":{"app":"1"},"abci":{"vote_extensions_enable_height":"0"}},
             "validators":[{"address":k["address"],"pub_key":k["pub_key"],"power":"10","name":f"validator-{i}"}
                           for i,k in enumerate(validator_keys)],"app_hash":"","app_state":plan}
    write_json(root/"genesis.json",genesis)
    rpcs=[f"http://10.89.90.{10+i}:26657" for i in range(4)]
    providers=[f"http://10.89.90.{20+i}:8900" for i in range(2)]
    nodes=[f"validator-{i}" for i in range(4)]+[f"provider-{i}" for i in range(2)]+[f"worker-{i}" for i in range(workers)]+["publisher"]
    for name in nodes:
        directory=root/name;directory.mkdir(exist_ok=True)
        write_json(directory/"plan.json",plan)
        write_json(directory/"genesis.json",genesis)
        write_json(directory/"cluster.json",{"plan_sha256":sha256(canonical_json(plan)),"genesis_sha256":sha256(canonical_json(genesis)),
                                            "rpcs":rpcs,"providers":providers})
        if name.startswith(("provider","worker")) or name=="publisher":
            write_json(directory/"trust.json",original.trust)
            replica,_=open_node(directory)
            for entry in entries: replica.add(entry)
        if name.startswith("provider"):
            write_json(directory/"artifact-index.json",files)
            for digest in files: shutil.copyfile(published.root/digest,directory/"objects"/digest)
    for i in range(4):
        directory=root/f"validator-{i}"/"comet"
        (directory/"config").mkdir(parents=True);(directory/"data").mkdir()
        write_json(directory/"config/genesis.json",genesis)
        write_json(directory/"config/priv_validator_key.json",validator_keys[i])
        write_json(directory/"config/node_key.json",{"priv_key":node_keys[i]["priv_key"]})
        write_json(directory/"data/priv_validator_state.json",{"height":"0","round":0,"step":0})
        peers=",".join(f"{k['address'].lower()}@10.89.90.{10+j}:26656" for j,k in enumerate(node_keys) if j!=i)
        configuration=f'''proxy_app = "tcp://127.0.0.1:26658"
moniker = "validator-{i}"
log_level = "error"
db_backend = "goleveldb"
db_dir = "data"
abci = "socket"
[rpc]
laddr = "tcp://0.0.0.0:26657"
max_open_connections = 64
max_body_bytes = 1048576
unsafe = false
[p2p]
laddr = "tcp://0.0.0.0:26656"
external_address = "10.89.90.{10+i}:26656"
persistent_peers = "{peers}"
addr_book_strict = false
allow_duplicate_ip = true
pex = false
[mempool]
size = 1000
max_txs_bytes = 33554432
max_tx_bytes = 65536
[consensus]
timeout_propose = "2s"
timeout_prevote = "1s"
timeout_precommit = "1s"
timeout_commit = "1s"
create_empty_blocks = true
create_empty_blocks_interval = "1s"
'''
        (directory/"config/config.toml").write_text(configuration,encoding="utf-8")
    # Host client has its own key and outbox, never a validator private key.
    for name in ["publisher",f"worker-{workers-1}"]:
        host={"plan_sha256":sha256(canonical_json(plan)),"genesis_sha256":sha256(canonical_json(genesis)),
              "rpcs":[f"http://127.0.0.1:{8920+i}" for i in range(4)],
              "providers":["http://127.0.0.1:8930","http://127.0.0.1:8931"]}
        (root/name/"cluster.json").write_bytes(canonical_json(host))
    report={"schema":"dawl.cluster-deployment/1","chain_id":plan["chain_id"],"workers":workers,
            "plan_sha256":sha256(canonical_json(plan)),"genesis_sha256":sha256(canonical_json(genesis)),
            "lineage_checkpoint":original.checkpoint(),"books":len(books),"chunks":len(records),
            "tokens":training_manifest["tokens"],"jobs":len(jobs),"replicas":replicas,
            "validators":4,"quorum":3,"host_worker":f"worker-{workers-1}","nodes":nodes,
            "scope":"Single-host deployment of actual BFT consensus; static admitted membership; no model training."}
    write_json(root/"deployment.json",report)
    return report
