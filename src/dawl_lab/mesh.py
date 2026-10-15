"""Lab fork of the bounded XOR discovery protocol, governed by a pinned LAN policy.

Signed, expiring multi-value role records are replicated at the closest peers.
Discovery never grants consensus/audit authority. No submitted code is executed.
"""
import asyncio
from contextlib import asynccontextmanager
import ipaddress
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI,Request,HTTPException
from data_ingestion.protocol import require_digest
from dawl_node.common import canonical_json,load_key,read_json,sha256,sign,verify_signature,write_json,parse_json

K=8
ALPHA=3
ROLES={'PROVIDER','TOKENIZER','TRAINER','TRAINING_VERIFIER','MODEL_HOST'}
NETWORK_POLICY=None


def configure_policy(policy,expected_digest):
    global NETWORK_POLICY
    if sha256(canonical_json(policy))!=expected_digest:raise ValueError('Network policy hash differs')
    if policy.get('schema')!='hypertrix.lab-network/1':raise ValueError('Unknown network policy')
    networks=[ipaddress.IPv4Network(v) for v in policy['allowed_cidrs']]
    if not networks or len(networks)>8:raise ValueError('Invalid network scope')
    private=[ipaddress.IPv4Network(v) for v in ('127.0.0.0/8','10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')]
    if any(not any(n.subnet_of(p) for p in private) or n.prefixlen<16 for n in networks):raise ValueError('Lab requires narrowly scoped private LANs')
    NETWORK_POLICY=policy


def atomic(path,value):
    pending=path.with_suffix('.pending')
    with pending.open('wb') as stream:
        stream.write(canonical_json(value));stream.flush();os.fsync(stream.fileno())
    pending.replace(path)


def peer_id(public):return sha256(bytes.fromhex(public))[:40]
def role_key(network,role):return sha256((network+':role:'+role).encode())[:40]
def distance(a,b):return int(a,16)^int(b,16)


def route(value,port):
    uri=urlsplit(value)
    if uri.scheme!='http' or uri.port!=port or uri.path or uri.query or uri.fragment or uri.username or uri.password:
        raise ValueError('invalid discovery route')
    address=ipaddress.IPv4Address(uri.hostname)
    if NETWORK_POLICY is None or not any(address in ipaddress.IPv4Network(v) for v in NETWORK_POLICY['allowed_cidrs']):
        raise ValueError('route outside pinned lab networks')
    if address.is_multicast or address.is_unspecified:raise ValueError('Invalid peer address')
    return value


def validate(record,network,now=None):
    now=time.time() if now is None else now
    body=verify_signature(record,record['body']['signer'])
    fields={'schema','network','peer_id','name','roles','endpoint','provider','sequence','issued','expires','signer'}
    if body.get('schema') == 'hypertrix.lab-peer/2':
        fields.add('profiles')
        profiles=body['profiles']
        if not isinstance(profiles,list) or len(profiles)>32 or profiles!=sorted(set(profiles)):raise ValueError('invalid profile capabilities')
        for identity in profiles:require_digest(identity)
    if set(body)!=fields:
        raise ValueError('invalid discovery fields')
    if body['schema'] not in ('hypertrix.lab-peer/1','hypertrix.lab-peer/2') or body['network']!=network or body['peer_id']!=peer_id(body['signer']):
        raise ValueError('wrong network or peer identity')
    if (not isinstance(body['name'],str) or not 1<=len(body['name'])<=40 or not isinstance(body['roles'],list)
        or not body['roles'] or body['roles']!=sorted(set(body['roles'])) or not set(body['roles'])<=ROLES
        or any(type(body[k]) is not int for k in ('sequence','issued','expires'))
        or body['sequence']<1 or body['issued']>now+30 or not now<body['expires']<=now+240
        or not 0<body['expires']-body['issued']<=180):raise ValueError('invalid discovery lifetime or roles')
    route(body['endpoint'],8913)
    if 'PROVIDER' in body['roles']:route(body['provider'],8911)
    elif body['provider'] is not None:raise ValueError('nonprovider artifact route')
    return body


class DHT:
    def __init__(self,root,network,name,host,roles,seeds):
        self.root=Path(root);self.network=network;self.name=name;self.host=host;self.roles=sorted(roles)
        self.key=load_key(self.root/'key.private.pem');self.public=self.key.public_key().public_bytes_raw().hex()
        self.id=peer_id(self.public);self.seeds=seeds;self.buckets={};self.values={};self.own=None
        self.client=httpx.AsyncClient(timeout=3,trust_env=False,follow_redirects=False)
        self.stats={'lookups':0,'stored':0,'rejected':0,'rpc_failures':0}

    def contacts(self):
        now=time.time()
        return [r for bucket in self.buckets.values() for r in bucket.values() if r['body']['expires']>now]

    def remember(self,record):
        body=validate(record,self.network)
        if body['peer_id']==self.id:return
        bucket=self.buckets.setdefault(distance(self.id,body['peer_id']).bit_length(),{})
        previous=bucket.get(body['peer_id'])
        if previous and body['sequence']<previous['body']['sequence']:raise ValueError('discovery sequence rollback')
        if previous and body['sequence']==previous['body']['sequence'] and record['id']!=previous['id']:
            raise ValueError('equivocating discovery sequence')
        for identity,old in list(bucket.items()):
            if old['body']['expires']<=time.time():del bucket[identity]
        if previous or len(bucket)<K:bucket[body['peer_id']]=record

    def closest(self,target):
        if len(target)!=40 or any(c not in '0123456789abcdef' for c in target):raise ValueError('invalid DHT key')
        values=self.contacts()+([self.own] if self.own else [])
        return sorted(values,key=lambda r:distance(target,r['body']['peer_id']))[:K]

    def store(self,key,record):
        body=validate(record,self.network)
        if key not in {role_key(self.network,r) for r in body['roles']}:raise ValueError('record is not for this role key')
        entries=self.values.setdefault(key,{})
        for identity,old in list(entries.items()):
            if old['body']['expires']<=time.time():del entries[identity]
        old=entries.get(body['peer_id'])
        if old and (body['sequence']<old['body']['sequence'] or (body['sequence']==old['body']['sequence'] and record['id']!=old['id'])):
            raise ValueError('stored record rollback or equivocation')
        if body['peer_id'] not in entries and len(entries)>=64:raise ValueError('bounded role registry is full')
        entries[body['peer_id']]=record;self.stats['stored']+=1

    async def rpc(self,url,payload):
        route(url,8913)
        try:
            async with self.client.stream('POST',url+'/dht/rpc',content=canonical_json(payload)) as response:
                response.raise_for_status();data=bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    # Up to 64 role values + 8 contacts, each with 32 profile IDs.
                    if len(data)>256*1024:raise ValueError('DHT response exceeds bound')
            value=parse_json(bytes(data))
            for record in value.get('nodes',[]):self.remember(record)
            return value
        except (httpx.HTTPError,OSError,ValueError,KeyError,TypeError):
            self.stats['rpc_failures']+=1;return {'nodes':[],'values':[]}

    async def lookup(self,target):
        self.stats['lookups']+=1;queried=set();found={}
        for _ in range(12):
            candidates=[r for r in self.closest(target) if r['body']['peer_id'] not in queried and r['body']['peer_id']!=self.id][:ALPHA]
            if not candidates:break
            for r in candidates:queried.add(r['body']['peer_id'])
            responses=await asyncio.gather(*(self.rpc(r['body']['endpoint'],{'op':'find','key':target,'sender':self.own}) for r in candidates))
            for response in responses:
                for record in response.get('values',[]):
                    try:
                        body=validate(record,self.network)
                        if target not in {role_key(self.network,role) for role in body['roles']}:continue
                        prior=found.get(body['peer_id'])
                        if prior is None or body['sequence']>prior['body']['sequence']:found[body['peer_id']]=record
                    except (ValueError,KeyError,TypeError):self.stats['rejected']+=1
        for record in self.values.get(target,{}).values():
            prior=found.get(record['body']['peer_id'])
            if record['body']['expires']>time.time() and (prior is None or record['body']['sequence']>prior['body']['sequence']):
                found[record['body']['peer_id']]=record
        return list(found.values())

    def announce(self):
        path=self.root/'dht-sequence.json';seq=read_json(path)['sequence']+1 if path.exists() else 1
        atomic(path,{'sequence':seq})
        now=int(time.time())
        profile_path=self.root/'profile-capabilities.json'
        extension={'schema':'hypertrix.lab-peer/2','profiles':read_json(profile_path)} if profile_path.exists() else {}
        self.own=sign({'schema':'hypertrix.lab-peer/1','network':self.network,'peer_id':self.id,'name':self.name,
            'roles':self.roles,'endpoint':'http://'+self.host+':8913',
            'provider':'http://'+self.host+':8911' if 'PROVIDER' in self.roles else None,
            'sequence':seq,'issued':now,'expires':now+180,**extension},self.key)
        validate(self.own,self.network,now)

    async def cycle(self):
        while True:
            self.announce()
            await asyncio.gather(*(self.rpc(url,{'op':'find','key':self.id,'sender':self.own}) for url in self.seeds))
            for role in self.roles:
                key=role_key(self.network,role);await self.lookup(key);self.store(key,self.own)
                await asyncio.gather(*(self.rpc(r['body']['endpoint'],{'op':'store','key':key,'record':self.own,'sender':self.own})
                    for r in self.closest(key) if r['body']['peer_id']!=self.id))
            atomic(self.root/'dht-status.json',{'peer_id':self.id,'contacts':len(self.contacts()),'role_keys':len(self.values),'stats':self.stats,'observed_at':time.time()})
            await asyncio.sleep(25)

    def app(self):
        @asynccontextmanager
        async def lifespan(app):
            task=asyncio.create_task(self.cycle())
            def failed(done):
                if not done.cancelled() and done.exception():
                    print('DHT maintenance failed: '+str(done.exception()),flush=True)
                    os.kill(os.getpid(),15)
            task.add_done_callback(failed)
            try:yield
            finally:task.cancel();await asyncio.gather(task,return_exceptions=True);await self.client.aclose()
        api=FastAPI(docs_url=None,openapi_url=None,redoc_url=None,lifespan=lifespan)
        @api.post('/dht/rpc')
        async def rpc(request:Request):
            try:
                data=bytearray()
                async for chunk in request.stream():
                    data.extend(chunk)
                    if len(data)>16384:raise ValueError('DHT request exceeds bound')
                value=parse_json(bytes(data));sender=value.get('sender')
                if sender is not None:self.remember(sender)
                key=value['key'];nodes=self.closest(key)
                if value['op']=='store':self.store(key,value['record'])
                elif value['op']!='find':raise ValueError('unknown DHT operation')
                return {'nodes':nodes,'values':[r for r in self.values.get(key,{}).values() if r['body']['expires']>time.time()]}
            except (ValueError,KeyError,TypeError):self.stats['rejected']+=1;raise HTTPException(400,'invalid discovery request')
        @api.get('/dht/roles/{role}')
        async def roles(role:str):
            if role not in ROLES:raise HTTPException(400,'unknown role')
            return {'records':await self.lookup(role_key(self.network,role))}
        @api.get('/dht/status')
        async def status():return {'peer_id':self.id,'contacts':len(self.contacts()),'stats':self.stats,'roles':self.roles}
        return api


if __name__=='__main__':
    import argparse
    import json
    import uvicorn
    from dawl_node.common import keygen
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--network',type=Path,required=True)
    p.add_argument('--network-id',required=True);p.add_argument('--name',required=True);p.add_argument('--advertise',required=True)
    p.add_argument('--bind',default='127.0.0.1');p.add_argument('--roles',default='PROVIDER');p.add_argument('--seeds',default='')
    a=p.parse_args();policy=json.loads(a.network.read_bytes());configure_policy(policy,a.network_id)
    route('http://'+a.advertise+':8913',8913)
    seeds=a.seeds.split(',') if a.seeds else []
    for seed in seeds:route(seed,8913)
    a.root.mkdir(parents=True,exist_ok=True)
    if not (a.root/'key.private.pem').exists():keygen(a.root/'key.private.pem',a.root/'key.public.hex')
    dht=DHT(a.root,a.network_id,a.name,a.advertise,a.roles.split(','),seeds)
    uvicorn.run(dht.app(),host=a.bind,port=8913,access_log=False,limit_concurrency=64)
