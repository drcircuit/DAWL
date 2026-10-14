import copy
from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from public_snapshot import validate_head,GENESIS_PIN


class PublicationGateTests(unittest.TestCase):
    def test_scope_age_rollback_and_conflict(self):
        now=datetime(2026,10,14,tzinfo=timezone.utc)
        c={'genesis_sha256':GENESIS_PIN,'state':{'height':9,'chain_id':'fixture'},'signed_header':{'header':{'height':'10','chain_id':'fixture','time':now.isoformat()}}}
        height,digest=validate_head(c,now=now)
        previous={'genesis_sha256':GENESIS_PIN,'height':height,'app_hash':digest}
        self.assertEqual(validate_head(c,previous,now)[0],9)
        for change in ('stale','chain','rollback','conflict'):
            bad=copy.deepcopy(c);p=dict(previous)
            if change=='stale':bad['signed_header']['header']['time']='2026-10-13T00:00:00Z'
            if change=='chain':bad['genesis_sha256']='0'*64
            if change=='rollback':p['height']=10
            if change=='conflict':p['app_hash']='0'*64
            with self.assertRaises(ValueError):validate_head(bad,p,now)

if __name__=='__main__':unittest.main()
