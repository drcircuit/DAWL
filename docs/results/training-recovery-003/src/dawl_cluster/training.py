"""Streaming next-token inputs and large checkpoint verification; no model training."""
import hashlib
from collections import OrderedDict
import numpy as np
from dawl_node.common import read_json, sha256
from dawl_platform.artifacts import Objects, PublicPeer


class TrainingInput:
    def __init__(self, manifest, objects, peer=None):
        if manifest["schema"] != "dawl.training-input/1" or manifest["dtype"] != "uint32-le":
            raise ValueError("unsupported training-input contract")
        self.manifest,self.objects,self.peer=manifest,objects,peer

    def sequences(self, length=128, split=None):
        if type(length) is not int or not 16<=length<=1024: raise ValueError("invalid sequence length")
        if split not in (None,"train","validation","test"): raise ValueError("unknown data split")
        cache=OrderedDict()
        for record in self.manifest["records"]:
            if split is not None and record["split"]!=split: continue
            file=record["file"]
            if file["sha256"] not in cache:
                self.objects.fetch(self.peer,file)
                cache[file["sha256"]]=np.frombuffer(self.objects.read(file),dtype="<u4")
                if len(cache)>4: cache.popitem(last=False)
            cache.move_to_end(file["sha256"])
            data=cache[file["sha256"]]
            offset,count=record["offset"],record["count"]
            if type(offset) is not int or type(count) is not int or offset<0 or count<1 or offset+count>len(data):
                raise ValueError("token span outside verified file")
            ids=data[offset:offset+count]
            if sha256(ids.tobytes())!=record["tokens_sha256"] or np.any(ids>=self.manifest["vocab_size"]):
                raise ValueError("training tokens differ from pinned chunk or vocabulary")
            for start in range(0,count-1,length):
                n=min(length,count-start-1)
                x=np.full(length,self.manifest["vocab_size"]-1,dtype=np.int64)
                y=x.copy();mask=np.zeros(length,dtype=np.bool_)
                x[:n]=ids[start:start+n];y[:n]=ids[start+1:start+n+1];mask[:n]=True
                yield {"inputs":x,"targets":y,"loss_mask":mask,"book_id":record["book_id"],
                       "source_index":record["source_index"],"split":record["split"],"token_offset":start}


def inspect(root, peer_url, length=128, verify_weights=False):
    plan=read_json(root/"plan.json")
    objects=Objects(root/"objects")
    peer=PublicPeer(peer_url,True)
    descriptor=plan["training_manifest"]
    objects.fetch(peer,descriptor)
    manifest=objects.read_json(descriptor)
    result={"schema":"dawl.training-input-check/1","chunks":manifest["chunks"],"source_tokens":manifest["tokens"],
            "sequence_length":length,"splits":{},"training_executed":False,"private_keys_used":False}
    for item in TrainingInput(manifest,objects,peer).sequences(length):
        bucket=result["splits"].setdefault(item["split"],{"sequences":0,"target_tokens":0,"books":set()})
        bucket["sequences"]+=1;bucket["target_tokens"]+=int(item["loss_mask"].sum());bucket["books"].add(item["book_id"])
    for value in result["splits"].values(): value["books"]=sorted(value["books"])
    splits=[set(v["books"]) for v in result["splits"].values()]
    if any(a & b for i,a in enumerate(splits) for b in splits[i+1:]): raise ValueError("book leaks across splits")
    if sum(v["target_tokens"] for v in result["splits"].values())!=manifest["tokens"]-manifest["chunks"]:
        raise ValueError("next-token targets have missing or duplicate coverage")
    if verify_weights:
        weight=plan["imported_weight"]["body"]
        objects.fetch(peer,weight["artifact"])
        descriptor=objects.read_json(weight["artifact"])
        whole=hashlib.sha256();total=0
        for block in descriptor["blocks"]:
            objects.fetch(peer,block)
            data=objects.read(block);whole.update(data);total+=len(data)
        if whole.hexdigest()!=weight["weights_sha256"] or total!=weight["bytes"]:
            raise ValueError("sharded checkpoint differs from registered whole-file identity")
        result["parent_weights"]={"sha256":whole.hexdigest(),"bytes":total,"blocks":len(descriptor["blocks"]),
                                  "historical_training_verified":False}
    return result
