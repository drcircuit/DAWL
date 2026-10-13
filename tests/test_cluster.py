"""Consensus application safety boundaries, deterministic recovery and streaming inputs."""
import base64
import copy
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from dawl_node.common import canonical_json, sha256, sign, write_json
from dawl_cluster.state import initial, advance, evaluate, complete, AUDIT, PUBLISH
from dawl_cluster.application import Application
from dawl_cluster.training import TrainingInput
from dawl_platform.artifacts import Objects


def fixture():
    keys=[Ed25519PrivateKey.generate() for _ in range(4)]
    public=[k.public_key().public_bytes_raw().hex() for k in keys]
    specs=[{"kind":"full-shard-replay/v1","vectors_entry":"a"*64,"book_id":11,"positions":[i],"tokens":10,"dense_bytes":160} for i in range(2)]
    plan={"schema":"dawl.cluster-plan/1","chain_id":"dawl-test","replicas":2,"lease_blocks":10,
          "actors":{p:{"name":str(i),"hats":[PUBLISH] if i==3 else [AUDIT]} for i,p in enumerate(public)},
          "books":[{"vectors_entry":"a"*64,"book_id":11,"chunks":2,"tokens":20}],
          "jobs":[{"id":sha256(canonical_json(s)),"spec":s} for s in specs],
          "implementation_sha256":"b"*64,"lineage_checkpoint":{"set_sha256":"c"*64},
          "training_manifest":{"sha256":"d"*64,"bytes":123},
          "imported_weight":{"id":"e"*64,"body":{"kind":"import","training_history_verified":False}}}
    return keys,public,plan


class StateTests(unittest.TestCase):
    def setUp(self):
        self.keys,self.public,self.plan=fixture()
        self.state=initial(self.plan)
        advance(self.state,1)

    def tx(self,who,kind,payload,state=None,**extra):
        s=state or self.state
        return sign({"schema":"dawl.cluster-tx/1","chain_id":s["chain_id"],"plan_sha256":s["plan_sha256"],
                     "sequence":s["actors"][self.public[who]]["sequence"]+1,"valid_until":s["height"]+60,
                     "kind":kind,"payload":payload,**extra},self.keys[who])

    def command(self,who,kind,payload):
        self.state,result=evaluate(self.state,self.tx(who,kind,payload))
        self.assertEqual(result["code"],0,result)
        return result["data"]

    def report(self,outcome="pass"):
        return {"outcome":outcome,"checked_chunks":1,"tokens_checked":10,"dense_bytes":160,
                "implementation_sha256":"b"*64,"execution_ms":2,"error":"" if outcome=="pass" else "fixture failure"}

    def finish(self,who,lease,outcome="pass"):
        return self.command(who,"result",{"job":lease["job"],"generation":lease["generation"],"report":self.report(outcome)})

    def test_claims_are_unique_per_actor_and_capacity_bounded(self):
        a=self.command(0,"claim",{"capability":AUDIT})
        b=self.command(0,"claim",{"capability":AUDIT})
        self.assertEqual(a,b)
        others=[self.command(i,"claim",{"capability":AUDIT}) for i in (1,2)]
        self.assertTrue(all(len(j["leases"])<=2 for j in self.state["jobs"].values()))
        self.assertEqual(sum(len(j["leases"]) for j in self.state["jobs"].values()),3)

    def test_stale_completion_is_fenced_after_reassignment(self):
        old=self.command(0,"claim",{"capability":AUDIT})
        for height in range(2,13): advance(self.state,height)
        self.assertEqual(self.state["expired_leases"],1)
        new=self.command(1,"claim",{"capability":AUDIT})
        before=canonical_json(self.state)
        self.state,result=evaluate(self.state,self.tx(0,"result",{"job":old["job"],"generation":old["generation"],"report":self.report()}))
        self.assertEqual(result["code"],1)
        self.assertEqual(before,canonical_json(self.state))
        self.assertNotEqual(new["generation"],old["generation"])

    def test_signature_role_sequence_and_cross_chain_replay_rejected(self):
        claim=self.tx(0,"claim",{"capability":AUDIT})
        self.state,result=evaluate(self.state,claim);self.assertEqual(result["code"],0)
        _,result=evaluate(self.state,claim);self.assertEqual(result["code"],1)
        for tx in [self.tx(3,"claim",{"capability":AUDIT}),self.tx(0,"publish_dataset",{}),
                   self.tx(1,"claim",{"capability":AUDIT},chain_id="other"),
                   self.tx(1,"claim",{"capability":AUDIT},valid_until=0)]:
            _,result=evaluate(self.state,tx);self.assertEqual(result["code"],1)
        forged=self.tx(1,"claim",{"capability":AUDIT});forged["body"]["sequence"]+=1
        _,result=evaluate(self.state,forged);self.assertEqual(result["code"],1)

    def test_false_coverage_and_wrong_worker_cannot_complete(self):
        lease=self.command(0,"claim",{"capability":AUDIT})
        payload={"job":lease["job"],"generation":lease["generation"],"report":self.report()}
        _,result=evaluate(self.state,self.tx(1,"result",payload));self.assertEqual(result["code"],1)
        for field,value in [("checked_chunks",2),("tokens_checked",11),("dense_bytes",0),("implementation_sha256","c"*64)]:
            bad=copy.deepcopy(payload);bad["report"][field]=value
            _,result=evaluate(self.state,self.tx(0,"result",bad));self.assertEqual(result["code"],1)
        self.assertFalse(any(complete(self.state,j) for j in self.state["jobs"].values()))

    def test_full_distinct_coverage_required_for_release_and_training_plan(self):
        _,result=evaluate(self.state,self.tx(3,"publish_dataset",{}));self.assertEqual(result["code"],1)
        for _ in range(4):
            for who in (0,1):
                lease=self.command(who,"claim",{"capability":AUDIT})
                if lease["job"]: self.finish(who,lease)
        self.assertTrue(all(complete(self.state,j) for j in self.state["jobs"].values()))
        release=self.command(3,"publish_dataset",{})
        payload={"dataset_release":release["dataset_release"],"parent_weight":"e"*64,
                 "recipe":{"executor":"causal-lm-next-token/v1","sequence_length":128,"seed":1,
                           "requires":"TRAINER_GPU","optimizer":"adamw","learning_rate":"0.0003"}}
        result=self.command(3,"plan_training",payload)
        self.assertFalse(self.state["training_plans"][result["training_plan"]]["executed"])
        _,result=evaluate(self.state,self.tx(3,"weight_candidate",{}));self.assertEqual(result["code"],1)

    def test_invalid_is_disputed_but_unavailable_can_retry(self):
        lease=self.command(0,"claim",{"capability":AUDIT});self.finish(0,lease,"unavailable")
        lease=self.command(0,"claim",{"capability":AUDIT});self.finish(0,lease,"invalid")
        self.assertFalse(complete(self.state,self.state["jobs"][lease["job"]]))
        _,result=evaluate(self.state,self.tx(3,"publish_dataset",{}));self.assertEqual(result["code"],1)

    def test_same_order_produces_same_hash_despite_dictionary_insertion_order(self):
        other=copy.deepcopy(self.state)
        other["jobs"]=dict(reversed(list(other["jobs"].items())))
        for who in (0,1,2):
            tx=self.tx(who,"claim",{"capability":AUDIT})
            self.state,_=evaluate(self.state,tx);other,_=evaluate(other,tx)
        self.assertEqual(canonical_json(self.state),canonical_json(other))

    def test_plan_rejects_gaps_overlaps_and_unadmitted_execution(self):
        bad=copy.deepcopy(self.plan);bad["jobs"].pop()
        with self.assertRaisesRegex(ValueError,"coverage"): initial(bad)
        bad=copy.deepcopy(self.plan);bad["jobs"].append(bad["jobs"][0])
        with self.assertRaises(ValueError): initial(bad)

    def test_malformed_and_deeply_nested_transactions_reject_without_mutation(self):
        nested=[]
        for _ in range(1100): nested=[nested]
        before=canonical_json(self.state)
        for tx in (None,[],True,{}, {"body":None},nested):
            after,result=evaluate(self.state,tx)
            self.assertEqual(result["code"],1)
            self.assertEqual(canonical_json(after),before)


class RecoveryTests(unittest.TestCase):
    def test_finalize_is_tentative_commit_survives_restart_and_replay(self):
        _,_,plan=fixture()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);write_json(root/"plan.json",plan)
            app=Application(root)
            answer=app.finalize(1,[])
            self.assertEqual(Application(root).info()["height"],0)
            app.commit()
            reopened=Application(root)
            self.assertEqual(reopened.info(),{"height":1,"hash":answer["hash"]})
            self.assertEqual(reopened.query("/state")["code"],0)
            self.assertEqual(reopened.query("/state",prove=True)["code"],1)
            with self.assertRaises(ValueError): reopened.finalize(3,[])
            with self.assertRaises(ValueError): reopened.init("different",base64.b64encode(canonical_json(plan)))


class TrainingTests(unittest.TestCase):
    def test_shifted_targets_masks_and_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            objects=Objects(Path(directory));ids=np.arange(50,dtype="<u4");file=objects.put(ids.tobytes())
            records=[{"file":file,"offset":o,"count":n,"tokens_sha256":sha256(ids[o:o+n].tobytes()),
                      "book_id":i,"source_index":0,"split":s} for i,(o,n,s) in enumerate([(0,21,"train"),(21,29,"test")])]
            manifest={"schema":"dawl.training-input/1","dtype":"uint32-le","vocab_size":100,"records":records}
            rows=list(TrainingInput(manifest,objects).sequences(16))
            self.assertEqual(sum(int(r["loss_mask"].sum()) for r in rows),48)
            for row in rows:
                valid=row["loss_mask"]
                np.testing.assert_array_equal(row["targets"][valid],row["inputs"][valid]+1)
                self.assertNotIn(21,row["targets"][valid] if row["split"]=="train" else [])
            manifest["records"][0]["count"]=1000
            with self.assertRaises(ValueError): list(TrainingInput(manifest,objects).sequences(16))


if __name__=="__main__": unittest.main()
