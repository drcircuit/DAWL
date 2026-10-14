"""Adversarial profile isolation, source preservation and tensor-layout checks."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tokenizers import Tokenizer, models, pre_tokenizers, decoders, normalizers
from dawl_node.common import canonical_json, keygen, load_key, sign
from dawl_profiles.profiles import make_profile, ProfileTokenizer, validate_weights, content_id
from dawl_profiles.workspace import Workspace, ProfileLedger


def fixture(workspace, label, nfc=False, dimension=4):
    vocab = {t: i for i, t in enumerate(sorted(pre_tokenizers.ByteLevel.alphabet()))}
    vocab['he'] = len(vocab)
    core = Tokenizer(models.BPE(vocab, [('h', 'e')]))
    core.pre_tokenizer = pre_tokenizers.Sequence([pre_tokenizers.Digits(individual_digits=True),
                                                pre_tokenizers.ByteLevel(add_prefix_space=False)])
    core.decoder = decoders.ByteLevel()
    if nfc: core.normalizer = normalizers.NFC()
    tok = core.to_str().encode()
    config = canonical_json({'model_type': 'fixture', 'vocab_size': 264, 'hidden_size': dimension})
    workspace.objects.put(tok); workspace.objects.put(config)
    return make_profile(tok, config, 'model.embed_tokens.weight', label)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.ws = Workspace(self.root)
        self.public = keygen(self.root/'key.pem', self.root/'public.key'); self.key = load_key(self.root/'key.pem')
        self.a = fixture(self.ws, 'raw'); self.b = fixture(self.ws, 'nfc', True, 7)
        self.trust = lambda p: {'schema': 'dawl.trust/1', 'profile_id': p['id'],
                               'identities': {self.public: ['source', 'tokenizer']}}
        self.la, self.ta = self.ws.open_profile(self.a, self.trust(self.a))
        self.lb, self.tb = self.ws.open_profile(self.b, self.trust(self.b))
        self.raw = ('hello cafe\u0301 \r\n \u00e6\u00f8\u00e5 \U0001f30d 12345\n'*200).encode()

    def test_coexistence_normalization_and_raw_object_reuse(self):
        ea = self.ws.ingest_book(self.la, self.ta, 11, self.raw, self.key)
        eb = self.ws.ingest_book(self.lb, self.tb, 11, self.raw, self.key)
        sa = self.la.get(ea['body']['payload']['data']['source_entry'])['body']['payload']['data']
        sb = self.lb.get(eb['body']['payload']['data']['source_entry'])['body']['payload']['data']
        self.assertEqual(sa['artifact'], sb['artifact'])
        self.assertEqual(self.ws.objects.read(sb['artifact']), self.raw)
        self.assertEqual(self.ws.verify_tokens(self.la, self.ta, ea)['normalized_frags'], 0)
        self.assertGreater(self.ws.verify_tokens(self.lb, self.tb, eb)['normalized_frags'], 0)
        for ledger, tok in ((self.la, self.ta), (self.lb, self.tb)):
            blueprint = self.ws.publish(ledger, tok, self.key)
            self.assertTrue(self.ws.verify_blueprint(ledger, tok, blueprint)['accepted'])
            self.assertEqual(ledger.verify_history(), ledger.checkpoint())
        self.assertNotEqual(self.la.policy, self.lb.policy)

    def test_cross_profile_entries_relabeling_and_tokenizers_fail(self):
        entry = self.ws.ingest_book(self.la, self.ta, 11, self.raw, self.key)
        with self.assertRaisesRegex(ValueError, 'policy'): self.lb.add(entry)
        changed = copy.deepcopy(entry['body']); changed['policy'] = self.lb.policy
        with self.assertRaisesRegex(ValueError, 'profile'): self.lb.add(sign(changed, self.key))
        with self.assertRaisesRegex(ValueError, 'profiles differ'):
            self.ws.ingest_book(self.la, self.tb, 76, self.raw, self.key)
        with self.assertRaisesRegex(ValueError, 'another model profile'):
            ProfileLedger(self.la.path, self.trust(self.a), self.b)

    def test_signed_foreign_manifest_and_mutated_tokens_fail(self):
        a = self.ws.ingest_book(self.la, self.ta, 11, self.raw, self.key)
        b = self.ws.ingest_book(self.lb, self.tb, 11, self.raw, self.key)
        changed = copy.deepcopy(a['body'])
        changed['payload']['data']['manifest'] = b['body']['payload']['data']['manifest']
        with self.assertRaisesRegex(ValueError, 'manifest profile'):
            self.ws.verify_tokens(self.la, self.ta, sign(changed, self.key))
        changed = copy.deepcopy(a['body'])
        manifest = self.ws.objects.read_json(changed['payload']['data']['manifest'])
        payload = bytearray(self.ws.objects.read(manifest['records'][0]['tokens'])); payload[0] ^= 1
        manifest['records'][0]['tokens'] = self.ws.objects.put(bytes(payload))
        changed['payload']['data']['manifest'] = self.ws.objects.json(manifest)
        with self.assertRaisesRegex(ValueError, 'retokenization'):
            self.ws.verify_tokens(self.la, self.ta, sign(changed, self.key))

    def test_blueprint_parent_scope_and_incomplete_coverage(self):
        self.ws.ingest_book(self.la, self.ta, 11, self.raw, self.key)
        first = self.ws.publish(self.la, self.ta, self.key)
        self.ws.ingest_book(self.la, self.ta, 76, b'next book', self.key)
        with self.assertRaisesRegex(ValueError, 'latest release'): self.ws.publish(self.la, self.ta, self.key)
        second = self.ws.publish(self.la, self.ta, self.key, first['id'])
        self.assertEqual(self.ws.verify_blueprint(self.la, self.ta, second)['books'], 2)
        with self.assertRaisesRegex(ValueError, 'policy'): self.ws.verify_blueprint(self.lb, self.tb, first)
        bad = copy.deepcopy(second['body']); bad['payload']['data']['frags'] += 1
        with self.assertRaisesRegex(ValueError, 'totals'): self.ws.verify_blueprint(self.la, self.ta, sign(bad, self.key))

    def test_padded_rows_runtime_and_model_binding(self):
        self.assertLess(self.ta.vocab_size, self.ta.rows)
        for field in ('version',):
            wrong = copy.deepcopy(self.a); wrong['body']['tokenizer'][field] = 'not-this-runtime'
            wrong['id'] = content_id(wrong['body'])
            with self.assertRaisesRegex(ValueError, 'runtime'):
                ProfileTokenizer(wrong, self.ws.objects.read(self.a['body']['tokenizer']['artifact']),
                                 self.ws.objects.read(self.a['body']['model']['config']))
        with self.assertRaisesRegex(ValueError, 'differs from profile'):
            ProfileTokenizer(self.a, self.ws.objects.read(self.b['body']['tokenizer']['artifact']),
                             self.ws.objects.read(self.a['body']['model']['config']))

    def test_named_tensor_shapes_dtypes_chunks_and_profile_binding(self):
        for profile, dtype, width in ((self.a, 'float32-le', 4), (self.b, 'bfloat16-le', 2)):
            emb = profile['body']['model']['input_embedding']; rows, dim = emb['shape']
            raw = bytes(rows*dim*width)
            manifest = {'schema': 'hypertrix.named-dense-weights/1', 'profile_id': profile['id'], 'tensors': {
                emb['name']: {'shape': [rows, dim], 'dtype': dtype,
                    'chunks': [self.ws.objects.put(raw[:100]), self.ws.objects.put(raw[100:])]},
                'layers.0.projection.weight': {'shape': [2, 3], 'dtype': 'float16-le',
                                             'chunks': [self.ws.objects.put(bytes(12))]}}}
            self.assertEqual(validate_weights(manifest, profile, self.ws.objects), content_id(manifest))
            bad = copy.deepcopy(manifest); bad['tensors'][emb['name']]['shape'][1] += 1
            with self.assertRaisesRegex(ValueError, 'byte length'): validate_weights(bad, profile, self.ws.objects)
            bad = copy.deepcopy(manifest); bad['profile_id'] = '0'*64
            with self.assertRaisesRegex(ValueError, 'another profile'): validate_weights(bad, profile, self.ws.objects)
            bad = copy.deepcopy(manifest); bad['tensors'][emb['name']]['dtype'] = 'opaque-quantized'
            with self.assertRaisesRegex(ValueError, 'unsupported'): validate_weights(bad, profile)


if __name__ == '__main__': unittest.main()
