from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_seed.model import Config,LanguageModel
from dawl_seed.run import configure,make_optimizer,update,save_checkpoint,load_checkpoint
from dawl_seed.intake import extract
from dawl_seed.groups import resolve
from dawl_node.common import keygen,write_json


class SeedTests(unittest.TestCase):
    def test_aliases_editions_and_coauthors_share_one_family(self):
        def doc(id,authors,filename='example.xml'):
            return {'id':id,'origin':{'authors':authors,'titles':['A title'],'archive_member':filename}}
        values=[doc('a',['>Ludvig Holberg']),doc('b',['Ludvig Holberg']),
                doc('c',['Helene Dickmar','Helene Dickmar']),doc('d',['Helene Dickmar (Hanna Butenschøn)']),
                doc('e',['Amalie Skram']),doc('f',['Amalie Skram','Erik Skram']),doc('g',['Erik Skram']),
                doc('h',[], 'NB_Collett_Brev_til_Ibsen_tekst.xml'),doc('i',['Camilla Collett'])]
        groups,policy=resolve(values)
        for family in [('a','b'),('c','d'),('e','f','g'),('h','i')]:self.assertEqual(len({groups[k] for k in family}),1)
    def test_tei_body_excludes_metadata_notes_and_external_entities(self):
        raw=('<?xml version="1.0"?><!DOCTYPE TEI SYSTEM "file:///unavailable"><TEI><teiHeader><title>NOT TRAINING</title></teiHeader>'
             '<text><body><p>'+('Norsk tekst. '*20)+'<note>NOT TRAINING</note><choice><sic>wrng</sic><corr>correct</corr></choice></p></body></text></TEI>').encode()
        text,meta=extract(raw)
        self.assertIn(b'correct',text);self.assertNotIn(b'NOT TRAINING',text);self.assertNotIn(b'wrng',text)
        with self.assertRaises(ValueError):extract(b'<!DOCTYPE a [<!ENTITY test "x">]><a/>')
        multiple=b'<TEI><text><group><text><body><p>'+b'First section. '*15+b'</p></body></text><text><body><p>Second section.</p></body></text></group></text></TEI>'
        self.assertIn(b'Second section.',extract(multiple)[0])

    def test_rms_swiglu_causality_and_resume(self):
        configure('cpu','fp32');torch.manual_seed(5)
        config=Config(vocab_size=260,context=16,width=32,heads=4,layers=2,hidden=64)
        model=LanguageModel(config)
        ids=torch.randint(3,260,(2,16));changed=ids.clone();changed[:,8:]=3
        self.assertTrue(torch.equal(model(ids)[0][:,:8],model(changed)[0][:,:8]))
        recipe={'learning_rate':.001,'pad_id':2,'max_steps':10,'router_balance_weight':.01,'runtime':{'precision':'fp32'}}
        values=np.random.default_rng(3).integers(3,260,size=(4,17));opt=make_optimizer(model,recipe)
        update(model,opt,values,[0,1],recipe,0,'cpu')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);write_json(root/'recipe.json',recipe);write_json(root/'config.json',config.__dict__)
            keygen(root/'operator.private.pem',root/'operator.public.key');save_checkpoint(root,model,opt,1)
            restored,resumed,_=load_checkpoint(root,1,'cpu',True)
            update(model,opt,values,[2,3],recipe,1,'cpu');update(restored,resumed,values,[2,3],recipe,1,'cpu')
            for k,v in model.state_dict().items():self.assertTrue(torch.equal(v,restored.state_dict()[k]),k)
            self.assertGreater(float(model.blocks[0].ff.gate.weight.grad.abs().sum()),0)
