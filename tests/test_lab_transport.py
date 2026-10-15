import json
from pathlib import Path
import tempfile
import time
import unittest
from fastapi.testclient import TestClient
from dawl_lab.artifacts import relative_path,app
from dawl_lab.mesh import configure_policy,route,DHT,validate
from dawl_node.common import keygen,sha256,canonical_json


class LabTransportTests(unittest.TestCase):
    def test_lan_scope_and_signed_roles(self):
        policy={'schema':'hypertrix.lab-network/1','allowed_cidrs':['127.80.0.0/24']};network=sha256(canonical_json(policy));configure_policy(policy,network)
        self.assertEqual(route('http://127.80.0.11:8913',8913),'http://127.80.0.11:8913')
        for url in ('http://169.254.169.254:8913','http://8.8.8.8:8913','http://127.80.0.11:22','http://user@127.80.0.11:8913','http://127.80.0.11:8913/path'):
            with self.assertRaises(ValueError):route(url,8913)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);keygen(root/'key.private.pem',root/'key.public.hex')
            dht=DHT(root,network,'lab-test','127.80.0.11',['PROVIDER','TRAINER'],[]);dht.announce()
            self.assertEqual(validate(dht.own,network)['roles'],['PROVIDER','TRAINER'])
            bad=json.loads(json.dumps(dht.own));bad['body']['roles']=['VALIDATOR']
            with self.assertRaises(ValueError):validate(bad,network)
            with self.assertRaises(ValueError):validate(dht.own,network,now=time.time()+300)

    def test_artifact_ranges_and_no_directory_escape(self):
        for value in ('../secret','/root/key','a/../../key','a\\b','a//b','a/private.pem'):
            with self.assertRaises(ValueError):relative_path(value)
        self.assertEqual(str(relative_path('run/operator.public.key')),'run/operator.public.key')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'objects').mkdir();payload=b'0123456789';digest=sha256(payload);(root/'objects'/digest).write_bytes(payload)
            with TestClient(app(root)) as client:
                result=client.get('/objects/'+digest,headers={'Range':'bytes=4-'})
                self.assertEqual(result.status_code,206);self.assertEqual(result.content,b'456789')
                self.assertEqual(client.get('/objects/not-a-hash').status_code,400)
                self.assertEqual(client.get('/objects/'+'0'*64).status_code,404)


if __name__=='__main__':unittest.main()
