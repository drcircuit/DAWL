"""Signed role discovery, routing and bootstrap loss over actual ASGI RPC handlers."""
import asyncio
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import httpx
from dawl_node.common import keygen,sign,write_json,canonical_json
from dawl_swarm.dht import DHT,validate,role_key,peer_id


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory();self.peers=[]
        for i,roles in enumerate([['LEDGER_REPLICA'],['AUDITOR'],['PROVIDER','TOKENIZER'],['AUDITOR']]):
            root=Path(self.directory.name)/str(i);root.mkdir();keygen(root/'key.private.pem',root/'public.key')
            peer=DHT(root,'a'*64,'node-'+str(i),'127.70.0.'+str(20+i),roles,[]);peer.announce();self.peers.append(peer)
        self.transports={'127.70.0.'+str(20+i):httpx.ASGITransport(app=p.app()) for i,p in enumerate(self.peers)}
        class Router(httpx.AsyncBaseTransport):
            async def handle_async_request(router,request):
                if request.url.host not in self.transports:raise httpx.ConnectError('offline')
                return await self.transports[request.url.host].handle_async_request(request)
        for peer in self.peers:
            await peer.client.aclose();peer.client=httpx.AsyncClient(transport=Router())

    async def asyncTearDown(self):
        for peer in self.peers:await peer.client.aclose()
        self.directory.cleanup()

    async def test_signed_lifetimes_roles_network_and_rollback(self):
        first,second,*_=self.peers;old=second.own;first.remember(old);second.announce();first.remember(second.own)
        with self.assertRaises(ValueError):first.remember(old)
        for changes in ({'network':'b'*64},{'expires':0},{'roles':['ROOT']},{'endpoint':'http://169.254.169.254:8903'}):
            with self.assertRaises(ValueError):validate(sign({**old['body'],**changes},second.key),'a'*64)
        with self.assertRaises(ValueError):first.store(role_key('a'*64,'PROVIDER'),second.own)
        forged={**second.own,'body':{**second.own['body'],'name':'forged'}}
        with self.assertRaises(ValueError):first.remember(forged)

    async def test_iterative_lookup_continues_after_original_bootstrap_is_gone(self):
        boot,relay,provider,joiner=self.peers;key=role_key('a'*64,'PROVIDER')
        boot.remember(relay.own);relay.remember(provider.own);provider.store(key,provider.own)
        await joiner.rpc(boot.own['body']['endpoint'],dict(op='find',key=joiner.id,sender=joiner.own))
        first=await joiner.lookup(key);self.assertEqual([r['id'] for r in first],[provider.own['id']])
        # Replicate the role record and remove both original bootstrap and provider transport.
        relay.store(key,provider.own)
        del self.transports[boot.host];del self.transports[provider.host]
        records=await joiner.lookup(key)
        self.assertEqual([r['id'] for r in records],[provider.own['id']])
        self.assertGreater(joiner.stats['rpc_failures'],0)

    async def test_versioned_profile_capabilities_are_signed_without_granting_roles(self):
        provider=self.peers[2]
        write_json(provider.root/'profile-capabilities.json',['a'*64,'b'*64]);provider.announce()
        body=validate(provider.own,'a'*64)
        self.assertEqual(body['schema'],'hypertrix.peer/2')
        self.assertEqual(body['profiles'],['a'*64,'b'*64])
        self.assertEqual(body['roles'],['PROVIDER','TOKENIZER'])
        for profiles in (['b'*64,'a'*64],['a'*64,'a'*64],['not-a-hash']):
            with self.assertRaises(ValueError):validate(sign({**body,'profiles':profiles},provider.key),'a'*64)

    async def test_full_profile_role_registry_fits_bounded_rpc_response(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        peer=self.peers[0]; template=self.peers[2].own['body']; records=[]
        for _ in range(64):
            key=Ed25519PrivateKey.generate(); public=key.public_key().public_bytes_raw().hex()
            body={**template,'schema':'hypertrix.peer/2','signer':public,'peer_id':peer_id(public),'name':'p'*40,
                  'profiles':[format(i,'064x') for i in range(32)]}
            record=sign(body,key);validate(record,'a'*64);records.append(record)
        data=canonical_json({'nodes':records[:8],'values':records})
        self.assertGreater(len(data),128*1024);self.assertLess(len(data),256*1024)
        await peer.client.aclose()
        peer.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=data)))
        answer=await peer.rpc(self.peers[2].own['body']['endpoint'],{'op':'find','key':role_key('a'*64,'PROVIDER')})
        self.assertEqual(len(answer['values']),64)


if __name__=='__main__':unittest.main()
