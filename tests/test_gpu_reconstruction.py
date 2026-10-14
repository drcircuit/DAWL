"""GPU-independent guards plus opt-in real CUDA lineage checks in the GPU image."""
import copy
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from gpu_reconstruction_node import assert_equal, ComparedTable, Sources
from dawl_node.common import sha256, load_key, sign
from dawl_platform.auditor import verify_book
import test_platform


class GPUChecks(unittest.TestCase):
    def test_changed_bit_is_not_a_tolerance_match(self):
        with self.assertRaises(ValueError): assert_equal(b'\x00', b'\x01')

    def test_corrupt_provider_fails_over_without_accepting_bad_bytes(self):
        class Peer:
            def __init__(self, data): self.data=data
            def request(self, *_): return self.data
        peers=Sources(['http://127.0.0.1'])
        peers.peers=[Peer(b'bad!'),Peer(b'good')]
        self.assertEqual(peers.request('/api/objects/'+sha256(b'good'),4),b'good')
        peers.peers=[Peer(b'bad!')]
        with self.assertRaises(OSError): peers.request('/api/objects/'+sha256(b'good'),4)


@unittest.skipUnless(os.environ.get('DAWL_TEST_CUDA')=='1','requires isolated CUDA test container')
class CUDALineageTests(unittest.TestCase):
    def setUp(self):
        test_platform.PipelineTests.setUp(self)
        vp=self.vector['body']['payload'];table_id=vp['table_entry']
        resource=self.ledger.get(table_id)['body']['payload']
        self.table=ComparedTable(self.objects.root/resource['files']['table']['sha256'],resource['descriptor'],lambda:False)
        self.addCleanup(self.table.gpu.close)
        self.tables={table_id:self.table}

    def test_released_fixture_matches_gpu_cpu_and_committed_index(self):
        result=verify_book(self.ledger,self.objects,None,self.vector,self.tables)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['checked_chunks'],self.table.gpu.calls)
        self.assertGreater(self.table.gpu.calls,0)

    def test_valid_signature_with_wrong_vector_commitment_is_rejected_on_gpu(self):
        wrong=copy.deepcopy(self.vector)
        index=self.objects.read_json(wrong['body']['payload']['files']['index'])
        index[0]['vectors_sha256']='f'*64
        wrong['body']['payload']['files']['index']=self.objects.json(index)
        wrong=sign(wrong['body'],load_key(self.root/'identities/vector/key.private.pem'))
        self.ledger.validate(wrong)
        with self.assertRaisesRegex(ValueError,'vector mismatch'):
            verify_book(self.ledger,self.objects,None,wrong,self.tables)


if __name__=='__main__': unittest.main()
