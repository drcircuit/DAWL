"""Corpus byte locks, metadata gates and exhaustive bounded audit coverage."""
import copy
from pathlib import Path
import tempfile
import unittest

from test_ingestion_nodes import make_fixture
from dawl_corpus.corpus import validate_book, prepare, partition_book
from dawl_corpus.coverage import audit_all
from dawl_node.common import canonical_json, write_json, sha256


class CorpusTests(unittest.TestCase):
    def test_partition_preserves_multibyte_characters_whitespace_and_byte_ranges(self):
        payload=('\ufeffA\r\n\u00e6\U0001f30d ' * 1000).encode('utf-8')
        book={"id":11,"source_url":"https://example.invalid/book.txt","sha256":sha256(payload)}
        for size in (4,7,513):
            parts=list(partition_book(book,payload,size))
            self.assertEqual(b''.join(d['text'].encode('utf-8') for d,_ in parts),payload)
            start=0
            for doc,mapping in parts:
                self.assertEqual(mapping['byte_start'],start)
                end=mapping['byte_end_exclusive']
                self.assertEqual(payload[start:end],doc['text'].encode('utf-8'))
                self.assertLessEqual(end-start,size)
                self.assertIn(book['sha256'],doc['source_uri'])
                start=end
            self.assertEqual(start,len(payload))

    def test_metadata_gate_rejects_wrong_identity_status_and_missing_notice(self):
        text = b'[eBook #11]\n*** START OF THE PROJECT GUTENBERG EBOOK ALICE ***\nBody\nTHE FULL PROJECT GUTENBERG LICENSE\n'
        rdf = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:pg="http://www.gutenberg.org/2009/pgterms/" xmlns:d="http://purl.org/dc/terms/"><pg:ebook rdf:about="ebooks/11"><d:rights>Public domain in the USA.</d:rights><d:language><rdf:Description><rdf:value>en</rdf:value></rdf:Description></d:language></pg:ebook></rdf:RDF>'''
        self.assertEqual(validate_book({"id":11}, text, rdf)["rights_declaration"], "Public domain in the USA.")
        trademark = text.replace(b'GUTENBERG LICENSE', 'GUTENBERG™ LICENSE'.encode('utf-8'))
        self.assertEqual(validate_book({"id":11}, trademark, rdf)["rights_declaration"], "Public domain in the USA.")
        for changed_text, changed_rdf in ((text.replace(b'#11',b'#12'),rdf),
                                          (text,rdf.replace(b'Public domain in the USA.',b'Copyrighted.')),
                                          (text.replace(b'THE FULL PROJECT GUTENBERG LICENSE',b''),rdf),
                                          (b'Used by permission.\n' + text,rdf)):
            with self.assertRaises(ValueError): validate_book({"id":11},changed_text,changed_rdf)

    def test_changed_book_bytes_fail_before_any_output_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'raw/11').mkdir(parents=True)
            (root/'raw/11/pg11.txt').write_bytes(b'changed')
            write_json(root/'lock.json',{"books":[{"id":11,"bytes":7,"sha256":"0"*64}]})
            with self.assertRaises(ValueError): prepare(root/'lock.json',root/'raw',root/'unused',"0"*64,root/'out')
            self.assertFalse((root/'out').exists())

    def test_batched_coverage_visits_every_chunk_once_and_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=make_fixture(Path(tmp))
            class Peer:
                broken=False
                def manifest(self,sid): return f['snapshot'].envelope
                def challenge(self,sid,challenge):
                    response=f['snapshot'].respond(challenge['nonce'],challenge['positions'])
                    if self.broken: response['samples'].pop()
                    return response,len(canonical_json(response))
            peer=Peer()
            result=audit_all(peer,f['report']['snapshot_id'],f['registry'],f['core'],f['public'],2)
            self.assertTrue(result['accepted'])
            positions=[p for batch in result['batches'] for p in batch['positions']]
            self.assertEqual(positions,list(range(f['report']['chunks'])))
            self.assertTrue(all(len(batch['positions'])<=2 for batch in result['batches']))
            self.assertTrue(all('single_bad_chunk_detection_probability' not in batch for batch in result['batches']))
            for size in (0,257,True):
                with self.assertRaises(ValueError): audit_all(peer,f['report']['snapshot_id'],f['registry'],f['core'],f['public'],size)
            peer.broken=True
            bad=audit_all(peer,f['report']['snapshot_id'],f['registry'],f['core'],f['public'],2)
            self.assertFalse(bad['accepted'])
            self.assertEqual(bad['verified_chunks'],0)
