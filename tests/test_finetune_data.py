import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_finetune.data import assignments, clean_bounds, segments, prepare, load
from dawl_node.common import write_json, sha256


class FixtureTokenizer:
    def encode(self,text,add_special_tokens=False):
        # Distinct position IDs give meaningful overlap checks across every window.
        return [int(x) for x in text.split()]


class DataTests(unittest.TestCase):
    def test_utf8_segment_boundaries_and_original_offsets(self):
        raw=('header\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\n'+'æøå text\n'*1000+
             '*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicense').encode()
        a,b=clean_bounds(raw); chunks=list(segments(raw,a,b,target=113))
        self.assertEqual(b''.join(raw[x:y] for x,y in chunks),raw[a:b])
        for x,y in chunks:raw[x:y].decode('utf-8')
        self.assertNotIn(b'license',raw[a:b])

    def test_all_splits_and_no_token_overlap_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);books=[]
            for index,domain in enumerate(['economics','literature']):
                raw=('*** START OF THE PROJECT GUTENBERG EBOOK X ***\n'+
                     '\n'.join(' '.join(str(n) for n in range(i,i+100)) for i in range(100000+index*100000,180000+index*100000,100))+
                     '\n*** END OF THE PROJECT GUTENBERG EBOOK X ***').encode()
                (root/f'{index}.txt').write_bytes(raw)
                books.append({'id':index,'domain':domain,'file':f'{index}.txt','sha256':sha256(raw)})
            write_json(root/'source-lock.json',{'books':books});write_json(root/'base-lock.json',{})
            prepare(root,FixtureTokenizer());data=load(root)
            sets={split:set(np.concatenate([data[f'{split}_{d}'].ravel() for d in ['economics','literature']]).tolist())
                  for split in ['train','validation','test']}
            self.assertFalse(sets['train'] & sets['validation']);self.assertFalse(sets['train'] & sets['test'])
            self.assertFalse(sets['validation'] & sets['test'])
            with (root/'dataset.npz').open('ab') as f:f.write(b'changed')
            with self.assertRaisesRegex(ValueError,'provenance mismatch'):load(root)

    def test_assignments_are_stable_and_have_holdouts(self):
        result=assignments(3300,100)
        self.assertEqual(result,assignments(3300,100))
        self.assertEqual([result.count(s) for s in ['train','validation','test']],[80,10,10])


if __name__=='__main__':unittest.main()
