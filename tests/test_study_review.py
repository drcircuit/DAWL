"""The shared review must preserve stimuli without publishing participant storage."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from build_study_review import build
from trust_study import digest


class ReviewPublicationTests(unittest.TestCase):
    def test_public_bundle_has_identical_stimuli_and_only_hashed_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manifest=build(root)
            data=json.loads((root/'data/review.json').read_text(encoding='utf-8'))
            protocol=json.loads((ROOT/'research/trust-study-v2.json').read_text(encoding='utf-8'))
            self.assertFalse(data['responses_collected']);self.assertEqual(len(data['trials']),18)
            for case in protocol['cases']:
                for variant in ('good','bad'):
                    views=[data['trials'][f"{case['id']}/{variant}/{c}"] for c in 'ABC']
                    self.assertEqual({v['advice'] for v in views},{case['advice'][variant]})
                    self.assertEqual({v['situation'] for v in views},{case['situation']})
                    b,c=views[1]['documentation'],json.loads(json.dumps(views[2]['documentation']))
                    for step in c['steps']:
                        name=step.pop('evidence');raw=(root/name).read_bytes()
                        self.assertEqual(hashlib.sha256(raw).hexdigest(),Path(name).stem)
                    self.assertEqual(b,c)
                    for view in views:
                        self.assertEqual(view['documentation_sha256'],digest(view['documentation']))
                        self.assertFalse({'provisional_answer','candidate_quality','intended_error','arithmetic'} & set(view))
            self.assertFalse(list(root.rglob('*.sqlite3')))
            self.assertEqual(set(manifest['files'])|{'manifest.json'},
                             {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()})
            for name,expected in manifest['files'].items():
                self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),expected)


if __name__=='__main__':unittest.main()
