import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src'),str(Path(__file__).resolve().parents[1]/'scripts')]
from fastapi.testclient import TestClient
from tokenizers import Tokenizer,models,pre_tokenizers,decoders,trainers
from dawl_node.common import keygen,write_json
from dawl_scratch.data import file_hash
from dawl_scratch import model as first_model,run as first_run
from dawl_seed import model as second_model,run as second_run
from seed_playground import application


class SeedRegistryTests(unittest.TestCase):
    def test_per_model_corpus_signed_selection_and_partial_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);old=base/'old';new=base/'new'
            tok=Tokenizer(models.BPE());tok.pre_tokenizer=pre_tokenizers.ByteLevel(add_prefix_space=False);tok.decoder=decoders.ByteLevel()
            tok.train_from_iterator(['Norwegian code example'],trainer=trainers.BpeTrainer(vocab_size=260,
                initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),special_tokens=['<|bos|>','<|eos|>','<|pad|>'],show_progress=False))
            for folder,targets in [(old,30),(new,40)]:
                data=folder/'dataset';data.mkdir(parents=True);tok.save(str(data/'tokenizer.json'))
                write_json(data/'blueprint.json',{'tokenizer_sha256':file_hash(data/'tokenizer.json'),'documents':3,'domains':{'code':3},'arrays':{'train':{'targets':targets}},'pad_id':2,'eos_id':1})
            for folder,model_module,run in [(old,first_model,first_run),(new,second_model,second_run)]:
                root=folder/'dense';root.mkdir()
                config=model_module.Config(vocab_size=260,context=16,width=32,heads=4,layers=2,hidden=64)
                recipe={'blueprint_sha256':file_hash(folder/'dataset/blueprint.json'),'learning_rate':.001}
                write_json(root/'config.json',config.__dict__);write_json(root/'recipe.json',recipe)
                keygen(root/'operator.private.pem',root/'operator.public.key');model=model_module.LanguageModel(config)
                run.save_checkpoint(root,model,run.make_optimizer(model,recipe),0)
            html=base/'index.html';html.write_text('<p>fixture</p>')
            # A descriptor written before its lineage record must not become selectable.
            pending=new/'dense/checkpoints/0000999';pending.mkdir()
            (pending/'checkpoint.json').write_bytes((new/'dense/checkpoints/0000000/checkpoint.json').read_bytes())
            with TestClient(application(old,old/'dataset',html,new)) as client:
                found={m['id']:m for m in client.get('/api/models').json()['models']}
                self.assertEqual(found['seed2']['step'],0);self.assertEqual(found['seed2']['corpus']['training_targets_per_epoch'],40)
                self.assertEqual(found['dense']['corpus']['training_targets_per_epoch'],30)
                self.assertEqual(found['seed2']['parameters'],sum(p.numel() for p in model.parameters()))
                body={'model':'seed2','prompt':'Norsk','max_tokens':4,'temperature':0}
                self.assertEqual(client.post('/api/complete',json=body).status_code,200)
                (new/'dense/recipe.json').write_text('{partial')
                self.assertEqual([m['id'] for m in client.get('/api/models').json()['models']],['dense'])
