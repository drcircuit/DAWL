"""Bootstrap isolation, pin checking, certified read policy and attributed incidents."""
import asyncio
import base64
import copy
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import test_cluster
from test_cluster import fixture
from dawl_node.common import canonical_json, read_json, sha256, sign, write_json
from dawl_platform.workflow import open_node
from dawl_cluster.external import identity, assemble, install, validate_registration
from dawl_cluster.client import Client
from dawl_cluster.state import advance, evaluate, initial
from dawl_cluster.application import Application


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        _,public,plan=fixture()
        self.template=self.root/"template"
        write_json(self.template/"trust.json",{"schema":"dawl.trust/1","identities":{public[0]:["source"]}})
        ledger,_=open_node(self.template)
        plan["lineage_checkpoint"]=ledger.checkpoint()
        write_json(self.template/"plan.json",plan);write_json(self.template/"artifact-index.json",{})
        roles=["validator"]*4+["provider","worker","worker","publisher"]
        self.nodes=[]
        for i,role in enumerate(roles):
            node=self.root/f"node-{i}"
            identity(node,f"node-{i}",role,f"10.55.0.{i+10}")
            self.nodes.append(node)
        self.bundle=self.root/"public"

    def assemble(self,**kwargs):
        return assemble(self.bundle,self.template,[n/"registration.json" for n in self.nodes],
                        kwargs.get("chain_id","dawl-external-test"),"example.org/dawl@sha256:"+"1"*64)

    def test_shared_genesis_distinct_keys_and_no_private_bundle(self):
        result=self.assemble()
        for node in self.nodes:
            install(node,self.bundle,result["network_sha256"])
        for p in self.bundle.iterdir():
            self.assertNotIn(b'"priv_key"',p.read_bytes());self.assertNotIn(b'PRIVATE KEY',p.read_bytes())
        gen={(n/"comet/config/genesis.json").read_bytes() for n in self.nodes[:4]}
        self.assertEqual(len(gen),1)
        self.assertEqual(len({(n/"comet/config/node_key.json").read_bytes() for n in self.nodes[:4]}),4)
        for node in self.nodes[:4]:
            config=(node/"comet/config/config.toml").read_text()
            self.assertEqual(config.count("@10.55.0."),3)
            self.assertNotIn("127.0.0.1:26657",config)
        self.assertTrue(read_json(self.nodes[5]/"cluster.json")["verify_state"])

    def test_install_rejects_wrong_pin_tampered_bundle_and_unadmitted_identity(self):
        result=self.assemble();node=self.nodes[0]
        with self.assertRaisesRegex(ValueError,"pin mismatch"): install(node,self.bundle,"0"*64)
        stranger=self.root/"stranger";identity(stranger,"stranger","worker","10.55.0.90")
        with self.assertRaisesRegex(ValueError,"not admitted"): install(stranger,self.bundle,result["network_sha256"])
        with (self.bundle/"plan.json").open("ab") as stream: stream.write(b" ")
        with self.assertRaisesRegex(ValueError,"file changed"): install(node,self.bundle,result["network_sha256"])

    def test_cannot_reinitialize_or_reset_used_validator(self):
        result=self.assemble();node=self.nodes[0]
        with self.assertRaisesRegex(ValueError,"exists"): identity(node,"node-0","validator","10.55.0.10")
        used=node/"comet/data/priv_validator_state.json"
        used.write_bytes(canonical_json({"height":"12","round":0,"step":3}))
        with self.assertRaisesRegex(ValueError,"already signed"): install(node,self.bundle,result["network_sha256"])
        self.assertEqual(read_json(used)["height"],"12")
        install(self.nodes[1],self.bundle,result["network_sha256"])
        with self.assertRaisesRegex(ValueError,"already installed"): install(self.nodes[1],self.bundle,result["network_sha256"])

    def test_fresh_chain_and_address_rules(self):
        with self.assertRaisesRegex(ValueError,"new chain"): self.assemble(chain_id="dawl-test")
        for host in ("127.0.0.1","0.0.0.0","8.8.8.8"):
            with self.assertRaises(ValueError): identity(self.root/"bad","bad","worker",host)
        self.assertFalse((self.root/"bad").exists())
        with self.assertRaises(ValueError): identity(self.root/"bad","../../bad","worker","10.0.0.1")

    def test_registration_proves_both_keys_and_cannot_change_address(self):
        reg=read_json(self.nodes[0]/"registration.json")
        validate_registration(reg)
        bad=copy.deepcopy(reg);bad["registration"]["body"]["host"]="10.55.0.99"
        with self.assertRaises(ValueError): validate_registration(bad)
        bad=copy.deepcopy(reg);bad["node_proof"]="00"*64
        with self.assertRaises(Exception): validate_registration(bad)


class CertifiedClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.keys,self.public,self.plan=fixture()
        write_json(self.root/"plan.json",self.plan);write_json(self.root/"genesis.json",{"fixture":True})
        self.config={"rpcs":["http://one","http://two"],"plan_sha256":sha256(canonical_json(self.plan)),
                     "genesis_sha256":sha256((self.root/"genesis.json").read_bytes()),
                     "verify_state":True,"max_certificate_age_seconds":120}
        write_json(self.root/"cluster.json",self.config)
        self.client=Client(self.root,self.keys[0]);self.addAsyncCleanup(self.client.close)

    def certificate(self,height):
        state=initial(self.plan)
        for h in range(1,height+1): advance(state,h)
        return {"verified":True,"state":state,"state_height":height,"genesis_sha256":self.config["genesis_sha256"],
                "app_hash":sha256(canonical_json(state))}

    async def test_failover_never_uses_unverified_rpc_state(self):
        self.client.certificate=AsyncMock(side_effect=[ValueError("invalid signature"),self.certificate(3)])
        result=await self.client.state()
        self.assertEqual(result["height"],3)
        self.assertEqual(self.client.certificate.await_count,2)
        self.client.certificate=AsyncMock(side_effect=ValueError("bad"))
        with self.assertRaises(OSError): await self.client.state()

    async def test_persistent_rollback_guard_and_tampered_state(self):
        self.client.accept_certificate(self.certificate(4))
        reopened=Client(self.root)
        try:
            with self.assertRaisesRegex(ValueError,"rollback"): reopened.accept_certificate(self.certificate(3))
            bad=self.certificate(5);bad["state"]["expired_leases"]=200
            with self.assertRaisesRegex(ValueError,"pins"): reopened.accept_certificate(bad)
            self.assertEqual(reopened.accept_certificate(self.certificate(5))["height"],5)
        finally: await reopened.close()

    async def test_untrusted_rejection_cannot_erase_outbox_before_certified_ack(self):
        tx=sign({"schema":"dawl.cluster-tx/1","chain_id":self.plan["chain_id"],
                 "plan_sha256":self.config["plan_sha256"],"sequence":1,"valid_until":60,
                 "kind":"claim","payload":{"capability":"AUDIT_CPU"}},self.keys[0])
        with closing(self.client.connect()) as db,db:
            db.execute("INSERT INTO outbox VALUES (1,?)",(canonical_json(tx),))
        before=self.certificate(1)["state"]
        after=copy.deepcopy(before)
        after["actors"][self.public[0]].update(last_tx=tx["id"],sequence=1,last_result={"committed":True})
        calls=0
        async def observed():
            nonlocal calls
            calls+=1
            if calls==1: return before
            with closing(self.client.connect()) as db:
                self.assertIsNotNone(db.execute("SELECT payload FROM outbox").fetchone())
            return after
        self.client.state=observed
        self.client.rpc=AsyncMock(return_value={"code":1,"log":"dishonest rejection"})
        self.assertEqual(await self.client.submit(tx),{"committed":True})
        with closing(self.client.connect()) as db:
            self.assertIsNone(db.execute("SELECT payload FROM outbox").fetchone())


class IncidentTests(test_cluster.StateTests):
    # Inherit the existing policy tests to check the optional incident contract too.
    def setUp(self):
        super().setUp();self.plan["evidence_contract"]="dawl.incidents/1"
        self.state=initial(self.plan);advance(self.state,1)

    def test_timeout_is_attributed_without_punishment(self):
        lease=self.command(0,"claim",{"capability":"AUDIT_CPU"})
        for height in range(2,13): advance(self.state,height)
        event=self.state["incidents"]["recent"][-1]
        self.assertEqual(event["kind"],"lease_expired")
        self.assertEqual(event["detail"]["signer"],self.public[0])
        self.assertFalse(event["detail"]["misconduct_proven"])
        self.assertEqual(self.state["plan"]["actors"],self.plan["actors"])

    def test_conflict_requires_signed_contradiction_and_committed_assignment(self):
        lease=self.command(0,"claim",{"capability":"AUDIT_CPU"})
        payload={"job":lease["job"],"generation":lease["generation"],"report":self.report()}
        a=self.tx(0,"result",payload)
        bad=copy.deepcopy(payload);bad["report"]=self.report("invalid")
        b=self.tx(0,"result",bad)
        evidence={"results":[a,b]}
        _,result=evaluate(self.state,self.tx(3,"record_worker_conflict",evidence))
        self.assertEqual(result["code"],1)
        self.state,result=evaluate(self.state,a);self.assertEqual(result["code"],0)
        result=self.command(3,"record_worker_conflict",evidence)
        self.assertIsNone(result["punishment"])
        self.command(3,"record_worker_conflict",{"results":[b,a]})
        self.assertEqual(len(self.state["incidents"]["worker_conflicts"]),1)
        _,result=evaluate(self.state,self.tx(3,"record_worker_conflict",{"results":[a,a]}))
        self.assertEqual(result["code"],1)
        forged=copy.deepcopy(b);forged["signature"]="00"*64
        _,result=evaluate(self.state,self.tx(3,"record_worker_conflict",{"results":[a,forged]}))
        self.assertEqual(result["code"],1)

    def test_engine_evidence_is_durable_only_after_commit(self):
        report={"type":1,"validator":{"address":base64.b64encode(b'x'*20).decode(),"power":10},
                "height":1,"time":"2026-10-13T00:00:00Z","total_voting_power":40}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);write_json(root/"plan.json",self.plan)
            app=Application(root);app.finalize(1,[]);app.commit()
            app.finalize(2,[],[report])
            self.assertEqual(Application(root).state["incidents"]["counts"],{})
            app.commit()
            self.assertEqual(Application(root).state["incidents"]["counts"],{"consensus_misbehavior":1})


if __name__=="__main__": unittest.main()
