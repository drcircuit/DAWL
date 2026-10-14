"""Pinned ordinary encoding matches upstream, offline, with adversarial inputs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from dawl_profiles.profiles import make_o200k_profile, ProfileTokenizer, validate_weights
from dawl_profiles.o200k import PATTERN, SPECIAL_TOKENS


class O200kTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1]/'artifacts/tokenizer-candidates/o200k_base/o200k_base.tiktoken'
        if not path.exists(): raise unittest.SkipTest('Run scripts/fetch_o200k.py for the pinned public ranks')
        cls.payload = path.read_bytes(); cls.profile = make_o200k_profile(cls.payload)

    def test_matches_upstream_definition_and_encoding_without_network(self):
        import tiktoken_ext.openai_public as upstream
        from tiktoken import Encoding
        tokenizer = ProfileTokenizer(self.profile, self.payload)
        with patch.object(upstream, 'load_tiktoken_bpe', return_value=tokenizer.core._mergeable_ranks):
            definition = upstream.o200k_base()
        self.assertEqual(definition['pat_str'], PATTERN); self.assertEqual(definition['special_tokens'], SPECIAL_TOKENS)
        reference = Encoding(**definition)
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')):
            for text in ['', 'Hei \u00e6\u00f8\u00e5', 'cafe\u0301 \r\n', '\u4f60\u597d', '\U0001f469\u200d\U0001f4bb', '\x00', '<|endoftext|><|endofprompt|>']:
                normalized, ids = tokenizer.encode(text)
                self.assertEqual(normalized, text); self.assertEqual(ids, reference.encode_ordinary(text))
                self.assertFalse(set(ids).intersection(SPECIAL_TOKENS.values()))
        self.assertIsNone(tokenizer.dimension)

    def test_mutated_ranks_runtime_and_fake_weights_rejected(self):
        with self.assertRaises(ValueError): ProfileTokenizer(self.profile, self.payload+b'\n')
        with self.assertRaises(ValueError): validate_weights({}, self.profile)
        with self.assertRaises(ValueError): ProfileTokenizer(self.profile, self.payload, b'{}')
        with self.assertRaises(UnicodeError): ProfileTokenizer(self.profile, self.payload).encode('\ud800')


if __name__ == '__main__': unittest.main()
