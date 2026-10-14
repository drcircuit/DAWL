import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from tokenizers import Tokenizer
from dawl_node.common import write_json,sha256
from dawl_scratch.data import prepare,load


class DatasetTests(unittest.TestCase):
    def test_whole_source_splits_roundtrip_and_tampering(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);books=base/'books';code=base/'code';target=base/'dataset'
            books.mkdir();(code/'objects').mkdir(parents=True)
            entries=[]
            for i in range(6):
                raw=('*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\r\n'+
                     (f'Book {i}: æøå economics prose number {i}.\r\n'*120)+
                     '*** END OF THE PROJECT GUTENBERG EBOOK TEST ***').encode()
                (books/f'{i}.txt').write_bytes(raw)
                entries.append({'id':i,'file':f'{i}.txt','sha256':sha256(raw),'domain':'economics' if i<3 else 'literature'})
            write_json(books/'source-lock.json',{'books':entries})
            shared=b'// exact shared source across three repository families\nreturn 123;\n'
            shared_hash=sha256(shared);(code/'objects'/shared_hash).write_bytes(shared)
            for i in range(3):
                raw=(f'function example{i}(n) {{ return n + {i}; }}\r\n'*80).encode()
                digest=sha256(raw);(code/'objects'/digest).write_bytes(raw)
                write_json(code/f'repositories/repo{i}/snapshot.json',{'full_name':f'owner/repo{i}',
                    'family':f'owner/repo{i}','status':'candidate','commit':str(i)*40,'license':'MIT','rights_basis':'fixture',
                    'files':[{'path':'main.js','sha256':digest,'bytes':len(raw),'source_url':'https://example.test/source'},
                             {'path':'shared.js','sha256':shared_hash,'bytes':len(shared),'source_url':'https://example.test/shared'}]})
            write_json(code/'corpus-lock.json',{'fixture':True})
            result=prepare(target,books,code,vocab_size=512,context=32)
            blueprint,arrays=load(target)
            self.assertEqual(blueprint,result)
            self.assertTrue(all(len(v)>0 for v in arrays.values()))
            sources=json.loads((target/'sources.json').read_bytes())['documents']
            groups={}
            for doc in sources:groups.setdefault(doc['group'],set()).add(doc['split'])
            self.assertTrue(all(len(v)==1 for v in groups.values()))
            duplicates=[doc for doc in sources if doc['source_sha256']==shared_hash]
            self.assertEqual(len(duplicates),1)
            self.assertEqual(duplicates[0]['split'],'test')
            tok=Tokenizer.from_file(str(target/'tokenizer.json'))
            sample='Unseen 🦉 code\r\n\treturn 17;'
            self.assertEqual(tok.decode(tok.encode(sample).ids),sample)
            for array in arrays.values():array._mmap.close()
            with (target/'train.npy').open('ab') as stream:stream.write(b'x')
            with self.assertRaisesRegex(ValueError,'Token array hash mismatch'):load(target)


if __name__=='__main__':unittest.main()
