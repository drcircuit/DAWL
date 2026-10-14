"""Public bootstrap ceremony and operator-local installation for a fresh fixed-set chain.

Private keys never enter the assembled network bundle. Transport is deliberately
limited to a preconfigured private IPv4 network (for example a WireGuard mesh).
This module does not configure a VPN or migrate running validator state.
"""
import base64
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from dawl_node.common import (canonical_json, keygen, load_key, read_json, sha256,
                              sign, verify_signature, write_json)
from dawl_platform.workflow import implementation_digest, open_node
from .state import AUDIT, PUBLISH, initial

ROLES = {"validator", "worker", "publisher", "provider"}
NETWORKS = tuple(ipaddress.ip_network(s) for s in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"))
NODE_DOMAIN = b"DAWL/external-node-possession/1\x00"
TUNNEL_NETWORK = ipaddress.ip_network("127.70.0.0/24")


def valid_host(host, transport):
    address = ipaddress.IPv4Address(host)
    if transport == "ssh-loopback/1":
        return address in TUNNEL_NETWORK and address not in (TUNNEL_NETWORK.network_address, TUNNEL_NETWORK.broadcast_address)
    return transport is None and any(address in net for net in NETWORKS)


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(canonical_json(value))


def comet_key(key):
    public = key.public_key().public_bytes_raw()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return {"address": sha256(public)[:40].upper(),
            "pub_key": {"type": "tendermint/PubKeyEd25519", "value": base64.b64encode(public).decode()},
            "priv_key": {"type": "tendermint/PrivKeyEd25519", "value": base64.b64encode(seed+public).decode()}}


def validate_registration(registration):
    if set(registration) != {"registration", "node_proof"}:
        raise ValueError("unexpected registration fields")
    envelope = registration["registration"]
    body = verify_signature(envelope, envelope["body"]["signer"])
    fields = {"schema", "name", "role", "host", "node_public", "signer"}
    if body.get("schema") == "dawl.external-identity/2": fields.add("transport")
    if set(body) != fields:
        raise ValueError("unexpected identity fields")
    if body["schema"] not in ("dawl.external-identity/1", "dawl.external-identity/2") or body["role"] not in ROLES:
        raise ValueError("unsupported operator identity")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", body["name"]):
        raise ValueError("invalid operator name")
    if body["schema"].endswith("/2") and body.get("transport") != "ssh-loopback/1":
        raise ValueError("unsupported identity transport")
    if not valid_host(body["host"], body.get("transport")):
        raise ValueError("use an explicit private IPv4 mesh address")
    if body["role"] == "validator":
        raw = bytes.fromhex(body["node_public"])
        if len(raw) != 32 or body["node_public"] == body["signer"]:
            raise ValueError("distinct validator and P2P keys required")
        Ed25519PublicKey.from_public_bytes(raw).verify(bytes.fromhex(registration["node_proof"]),
                                                     NODE_DOMAIN+canonical_json(envelope))
    elif body["node_public"] is not None or registration["node_proof"] is not None:
        raise ValueError("non-validator cannot register a consensus P2P key")
    return body


def identity(root, name, role, host, transport=None):
    root = Path(root)
    if root.exists():
        raise ValueError("identity root exists; never overwrite keys or signing state")
    # Check public fields before creating any private material.
    if role not in ROLES or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise ValueError("invalid role or name")
    if not valid_host(host, transport):
        raise ValueError("use an explicit private IPv4 mesh address")
    keygen(root/"key.private.pem", root/"public.key")
    key = load_key(root/"key.private.pem")
    node = Ed25519PrivateKey.generate() if role == "validator" else None
    body = {"schema": "dawl.external-identity/2" if transport else "dawl.external-identity/1", "name": name, "role": role,
            "host": host, "node_public": node.public_key().public_bytes_raw().hex() if node else None}
    if transport: body["transport"] = transport
    envelope = sign(body, key)
    registration = {"registration": envelope,
                    "node_proof": node.sign(NODE_DOMAIN+canonical_json(envelope)).hex() if node else None}
    validate_registration(registration)
    if node:
        private_json(root/"comet/config/priv_validator_key.json", comet_key(key))
        private_json(root/"comet/config/node_key.json", {"priv_key": comet_key(node)["priv_key"]})
        private_json(root/"comet/data/priv_validator_state.json", {"height": "0", "round": 0, "step": 0})
    write_json(root/"registration.json", registration)
    return {"public_registration": str(root/"registration.json"), "name": name, "role": role}


def assemble(root, template, registrations, chain_id, image, role_images=None):
    """Build a public bundle from a pinned public corpus plan and signed identities."""
    root, template = Path(root), Path(template)
    if root.exists():
        raise ValueError("network bundle exists; create a fresh chain in a new directory")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{2,49}", chain_id):
        raise ValueError("invalid chain ID")
    if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("deployment image must be pinned by registry digest")
    if role_images is not None:
        if set(role_images) != ROLES or any(not isinstance(v,str) or not re.fullmatch(
                r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}",v) for v in role_images.values()):
            raise ValueError("exactly one immutable image pin per node role is required")
    public = [read_json(Path(path)) for path in registrations]
    members = [validate_registration(r) for r in public]
    if len({m.get("transport") for m in members}) != 1:
        raise ValueError("all members must agree on one transport profile")
    if not 8 <= len(members) <= 64:
        raise ValueError("external bootstrap supports 8 to 64 admitted identities")
    for field in ("name", "signer", "host"):
        if len({b[field] for b in members}) != len(members):
            raise ValueError(f"duplicate {field}; each role needs its own identity and mesh address")
    validators = sorted((b for b in members if b["role"] == "validator"), key=lambda b:b["name"])
    if len(validators) != 4 or len({v["node_public"] for v in validators}) != 4:
        raise ValueError("exactly four distinct validator/P2P identities are required")
    if len({m["signer"] for m in members} | {v["node_public"] for v in validators}) != len(members)+4:
        raise ValueError("keys must not be shared between roles")
    if sum(m["role"] == "publisher" for m in members) != 1 or sum(m["role"] == "provider" for m in members) < 1:
        raise ValueError("one publisher and at least one provider required")
    old = read_json(template/"plan.json")
    if chain_id == old["chain_id"]:
        raise ValueError("fresh bootstrap requires a new chain ID; this is not a migration")
    plan = copy.deepcopy(old)
    plan.update(chain_id=chain_id, implementation_sha256=implementation_digest(),
                evidence_contract="dawl.incidents/1",
                actors={m["signer"]:{"name":m["name"], "hats":[AUDIT if m["role"] == "worker" else PUBLISH]}
                        for m in members if m["role"] in ("worker", "publisher")})
    initial(plan)
    ledger, _ = open_node(template, read_only=True)
    ledger.verify_history()
    if ledger.checkpoint() != plan["lineage_checkpoint"]:
        raise ValueError("template lineage differs from pinned plan")
    genesis = {"genesis_time": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
               "chain_id": chain_id, "initial_height": "1", "app_hash": "", "app_state": plan,
               "consensus_params":{"block":{"max_bytes":"1048576", "max_gas":"-1"},
                   "evidence":{"max_age_num_blocks":"100000", "max_age_duration":"172800000000000", "max_bytes":"65536"},
                   "validator":{"pub_key_types":["ed25519"]}, "version":{"app":"1"},
                   "abci":{"vote_extensions_enable_height":"0"}},
               "validators":[{"address":sha256(bytes.fromhex(v["signer"]))[:40].upper(),
                   "pub_key":{"type":"tendermint/PubKeyEd25519", "value":base64.b64encode(bytes.fromhex(v["signer"])).decode()},
                   "power":"10", "name":v["name"]} for v in validators]}
    root.mkdir(parents=True)
    for name,value in (("genesis.json",genesis), ("plan.json",plan), ("trust.json",ledger.trust),
                       ("lineage.json",list(ledger.entries())), ("registrations.json",public),
                       ("artifact-index.json",read_json(template/"artifact-index.json"))):
        write_json(root/name,value)
    manifest = {"schema":"dawl.external-network/1", "chain_id":chain_id, "image":image,
                "transport":members[0].get("transport") or "preconfigured-private-ipv4-mesh", "files":{
                    p.name:sha256(p.read_bytes()) for p in sorted(root.iterdir())}}
    if role_images is not None:
        manifest.update(schema="dawl.external-network/2",role_images=role_images)
    write_json(root/"network.json",manifest)
    return {"network_sha256":sha256(canonical_json(manifest)), "genesis_sha256":manifest["files"]["genesis.json"],
            "chain_id":chain_id, "members":len(members), "scope":"Public bootstrap only; no private keys or remote deployment"}


def check_bundle(bundle, pin):
    bundle = Path(bundle)
    manifest = read_json(bundle/"network.json")
    if sha256((bundle/"network.json").read_bytes()) != pin or manifest["schema"] not in ("dawl.external-network/1","dawl.external-network/2"):
        raise ValueError("network bundle pin mismatch")
    if manifest["schema"].endswith('/2') and (set(manifest.get('role_images',{})) != ROLES or any(
            not isinstance(v,str) or not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}",v)
            for v in manifest['role_images'].values())):
        raise ValueError('invalid role image pins')
    expected = {"genesis.json", "plan.json", "trust.json", "lineage.json", "registrations.json", "artifact-index.json"}
    if set(manifest["files"]) != expected:
        raise ValueError("unexpected bundle files")
    for name,digest in manifest["files"].items():
        if sha256((bundle/name).read_bytes()) != digest:
            raise ValueError(f"bundle file changed: {name}")
    return manifest


def install(root, bundle, pin, bind_host=None):
    """Install only into an unused local identity; never rewrite a live node."""
    root,bundle = Path(root),Path(bundle)
    manifest = check_bundle(bundle,pin)
    for name in ("cluster.json", "application.sqlite", "worker.sqlite", "compose.json", "install-started.json"):
        if (root/name).exists():
            raise ValueError("node already installed or installation incomplete; preserve state and inspect it")
    local = read_json(root/"registration.json")
    member = validate_registration(local)
    # A Kubernetes ClusterIP is a stable advertised address, not a pod interface.
    # The wildcard bind is explicit and must be protected by network policy.
    if bind_host not in (None, member["host"], "0.0.0.0"):
        raise ValueError("bind host must be the registered address or explicit wildcard")
    listen = bind_host or member["host"]
    tunnel = member.get("transport") == "ssh-loopback/1"
    if tunnel and bind_host not in (None, member["host"]):
        raise ValueError("tunnel nodes must bind only their loopback address")
    abci_host = member["host"] if tunnel else "127.0.0.1"
    private_unix = tunnel and manifest["schema"] == "dawl.external-network/2"
    key = load_key(root/"key.private.pem")
    if key.public_key().public_bytes_raw().hex() != member["signer"]:
        raise ValueError("local private key differs from public registration")
    registrations = read_json(bundle/"registrations.json")
    if local not in registrations:
        raise ValueError("local identity is not admitted in this bundle")
    members = [validate_registration(r) for r in registrations]
    validators = sorted((m for m in members if m["role"] == "validator"),key=lambda m:m["name"])
    plan = read_json(bundle/"plan.json")
    genesis = read_json(bundle/"genesis.json")
    if genesis["app_state"] != plan or plan["chain_id"] != manifest["chain_id"]:
        raise ValueError("genesis/plan mismatch")
    if plan["implementation_sha256"] != implementation_digest():
        raise ValueError("install using the exact implementation admitted by this plan")
    if member["role"] == "validator":
        if read_json(root/"comet/data/priv_validator_state.json") != {"height":"0", "round":0, "step":0}:
            raise ValueError("validator has already signed; this is not a migration command")
        if set(p.name for p in (root/"comet/data").iterdir()) != {"priv_validator_state.json"}:
            raise ValueError("validator consensus data already exists")
        if read_json(root/"comet/config/priv_validator_key.json") != comet_key(key):
            raise ValueError("validator key differs from registration")
        node_bytes = base64.b64decode(read_json(root/"comet/config/node_key.json")["priv_key"]["value"],validate=True)
        if len(node_bytes) != 64 or Ed25519PrivateKey.from_private_bytes(node_bytes[:32]).public_key().public_bytes_raw().hex() != member["node_public"]:
            raise ValueError("P2P key differs from registration")
    write_json(root/"install-started.json",{"network_sha256":pin})
    for name in ("plan.json", "genesis.json", "trust.json", "artifact-index.json"):
        write_json(root/name,read_json(bundle/name))
    ledger,_ = open_node(root)
    for entry in read_json(bundle/"lineage.json"):
        ledger.add(entry)
    if ledger.checkpoint() != plan["lineage_checkpoint"]:
        raise ValueError("installed lineage does not match plan")
    config = {"plan_sha256":manifest["files"]["plan.json"], "genesis_sha256":manifest["files"]["genesis.json"],
              "rpcs":[f"http://{v['host']}:26657" for v in validators],
              "providers":[f"http://{m['host']}:8900" for m in members if m["role"] == "provider"],
              "verify_state":True, "max_certificate_age_seconds":120, "network_sha256":pin,
              "abci_host":abci_host, "abci_unix":private_unix}
    write_json(root/"cluster.json",config)
    if member["role"] == "validator":
        write_json(root/"comet/config/genesis.json",genesis)
        peers = ",".join(f"{sha256(bytes.fromhex(v['node_public']))[:40]}@{v['host']}:26656"
                         for v in validators if v["name"] != member["name"])
        abci_endpoint = "unix://"+str(root/"sockets/abci.sock") if private_unix else f"tcp://{abci_host}:26658"
        toml = f'''proxy_app = "{abci_endpoint}"
moniker = "{member['name']}"
log_level = "error"
db_backend = "goleveldb"
db_dir = "data"
abci = "socket"
[rpc]
laddr = "tcp://{listen}:26657"
unsafe = false
max_open_connections = 64
max_body_bytes = 1048576
[p2p]
laddr = "tcp://{listen}:26656"
external_address = "{member['host']}:26656"
persistent_peers = "{peers}"
addr_book_strict = false
allow_duplicate_ip = {str(tunnel).lower()}
pex = false
[mempool]
size = 1000
max_txs_bytes = 33554432
max_tx_bytes = 65536
[consensus]
timeout_propose = "3s"
timeout_prevote = "2s"
timeout_precommit = "2s"
timeout_commit = "1s"
create_empty_blocks = true
create_empty_blocks_interval = "1s"
'''
        (root/"comet/config/config.toml").write_text(toml,encoding="utf-8")
    role = member["role"]
    command = (["validator","/node"] if role == "validator" else
               ["provider","--root","/node","--host",listen,"--port","8900","--allow-insecure-lab"] if role == "provider" else
               ["worker","--root","/node"] if role == "worker" else ["status","--root","/node"])
    service = {"image":manifest.get("role_images",{}).get(role,manifest["image"]), "network_mode":"host", "read_only":True,"init":True,
               "entrypoint":["python","/app/scripts/cluster_entrypoint.py"],
               "user":f"{os.getuid()}:{os.getgid()}" if hasattr(os,"getuid") else "1000:1000",
               "volumes":["./:/node"], "tmpfs":["/tmp:size=256m"], "cap_drop":["ALL"],
               "security_opt":["no-new-privileges:true"], "cpus":2, "mem_limit":"2g", "pids_limit":256,
               "restart":"unless-stopped" if role != "publisher" else "no", "command":command}
    write_json(root/"compose.json",{"services":{"node":service}})
    write_json(root/"installed.json",{"schema":"dawl.external-install/1", "network_sha256":pin,
                                      "name":member["name"], "role":role})
    return {"name":member["name"], "role":role, "genesis_sha256":config["genesis_sha256"], "compose":str(root/"compose.json")}


def seed_provider(root, objects):
    root,objects = Path(root),Path(objects)
    if read_json(root/"installed.json")["role"] != "provider":
        raise ValueError("artifact seeding is restricted to providers")
    index = read_json(root/"artifact-index.json")
    # The allowlisted bytes are copied, never a validator directory or a database.
    from dawl_platform.artifacts import Objects
    source, destination = Objects(objects), Objects(root/"objects")
    for digest,descriptor in index.items():
        payload = source.read(descriptor)
        if digest != descriptor["sha256"]:
            raise ValueError("artifact index mismatch")
        destination.put(payload)
    return {"verified_artifacts":len(index)}
