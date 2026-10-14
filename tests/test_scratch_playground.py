import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from fastapi.testclient import TestClient
from tokenizers import Tokenizer,models,pre_tokenizers,decoders,trainers
from dawl_node.common import keygen,write_json
from dawl_scratch.data import file_hash
from dawl_scratch.model import Config,LanguageModel
from dawl_scratch.run import make_optimizer,save_checkpoint
from dawl_scratch.serve import application


class PlaygroundTests(unittest.TestCase):
    def test_signed_checkpoint_generation_and_request_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);dataset=base/'dataset';dataset.mkdir();root=base/'dense';root.mkdir()
            tok=Tokenizer(models.BPE());tok.pre_tokenizer=pre_tokenizers.ByteLevel(add_prefix_space=False)
            tok.decoder=decoders.ByteLevel()
            tok.train_from_iterator(['function hello() { return 1; }'],trainer=trainers.BpeTrainer(vocab_size=260,
                initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),special_tokens=['<|bos|>','<|eos|>','<|pad|>'],show_progress=False))
            tok.save(str(dataset/'tokenizer.json'))
            write_json(dataset/'blueprint.json',{'tokenizer_sha256':file_hash(dataset/'tokenizer.json'),
                'documents':3,'domains':{'code':3},'arrays':{'train':{'targets':30}},'pad_id':2,'eos_id':1})
            recipe={'blueprint_sha256':file_hash(dataset/'blueprint.json'),'learning_rate':.001}
            config=Config(vocab_size=260,context=16,width=32,heads=4,layers=2,hidden=64)
            write_json(root/'recipe.json',recipe);write_json(root/'config.json',config.__dict__)
            keygen(root/'operator.private.pem',root/'operator.public.key')
            model=LanguageModel(config);save_checkpoint(root,model,make_optimizer(model,recipe),0)
            html=base/'index.html';html.write_text('<p>fixture</p>')
            with TestClient(application(base,dataset,html)) as client:
                self.assertEqual(client.get('/api/models').json()['models'][0]['id'],'dense')
                body={'model':'dense','prompt':'function x() {','max_tokens':4,'temperature':0,'seed':42}
                first=client.post('/api/complete',json=body)
                self.assertEqual(first.status_code,200)
                second=client.post('/api/complete',json=body).json()
                self.assertEqual(first.json()['text'],second['text'])
                self.assertEqual(client.post('/api/complete',json={**body,'max_tokens':100000}).status_code,422)
                self.assertEqual(client.post('/api/complete',json=body,headers={'Origin':'https://example.test'}).status_code,403)
                self.assertEqual(client.post('/api/complete',json={**body,'model':'../secret'}).status_code,404)


if __name__=='__main__':unittest.main()
