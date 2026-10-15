from pathlib import Path
import tempfile
import unittest
import numpy as np
from dawl_lab.corpus import book_text,normalized,text_flag
from dawl_lab.data import schedule
from dawl_node.common import write_json
from dawl_scratch.data import file_hash


class LabDataTests(unittest.TestCase):
    def test_book_boilerplate_and_unicode(self):
        raw=b'License\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\nOne line\ncontinues.\n\nNext.\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nLicense'
        self.assertEqual(book_text(raw),'One line continues.\n\nNext.')
        with self.assertRaises(ValueError):book_text(b'no markers')
        self.assertEqual(normalized(' e\u0301\r\n'),'\u00e9')
        self.assertEqual(text_flag('hello '*120+'<|eos|>'),'reserved-token')

    def test_token_balanced_schedule_and_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);ds=base/'dataset';out=base/'run';ds.mkdir();out.mkdir()
            values=np.array([[3,4,5,6,7],[4,5,6,7,8],[3,4,2,2,2],[4,5,2,2,2]],dtype='<u2')
            values.tofile(ds/'train.bin')
            for name in ('tokenizer.json','sources.json','source-lock.json','evaluation.json','corpus-plan.json'):write_json(ds/name,{})
            write_json(ds/'windows.json',[{'domain':'a','split':'train','start_row':0,'end_row':2},{'domain':'b','split':'train','start_row':2,'end_row':4}])
            bp={'context':4,'pad_id':2,'mixture':{'a':.5,'b':.5},'arrays':{'train':{'file':'train.bin','sha256':file_hash(ds/'train.bin'),'windows':4}}}
            for name,key in [('tokenizer.json','tokenizer_sha256'),('sources.json','sources_sha256'),('source-lock.json','source_lock_sha256'),('windows.json','windows_sha256'),('evaluation.json','evaluation_sha256'),('corpus-plan.json','plan_sha256')]:bp[key]=file_hash(ds/name)
            write_json(ds/'blueprint.json',bp)
            stats=schedule(out,ds,4,100,5);counts=stats['domain_target_tokens']
            self.assertLessEqual(abs(counts['a']-counts['b']),4)
            other=base/'again';other.mkdir();schedule(other,ds,4,100,5)
            self.assertEqual((out/'schedule.npy').read_bytes(),(other/'schedule.npy').read_bytes())
            with (ds/'train.bin').open('ab') as f:f.write(b'x')
            with self.assertRaises(ValueError):schedule(base,ds,4,100,5)


if __name__=='__main__':unittest.main()
