from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_corpus.github import inspect_archive, path_reason, content_reason


class CorpusTests(unittest.TestCase):
    def test_archive_is_inert_and_records_exclusions(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);archive=root/'source.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('repo/main.py','print("never execute")\n')
                z.writestr('repo/.env','TOKEN=example')
                z.writestr('repo/node_modules/test.js','bad')
                z.writestr('repo/LICENSE','MIT license fixture')
                z.writestr('repo/../../escape.py','bad')
                z.writestr('repo/binary.py',b'\x00binary')
            files,excluded,licenses=inspect_archive(archive,root,{'full_name':'example/repo'},'a'*40)
            self.assertEqual([f['path'] for f in files],['main.py'])
            self.assertEqual(len(licenses),1)
            self.assertEqual(len(excluded),4)
            self.assertEqual((root/'objects'/files[0]['sha256']).read_bytes(),b'print("never execute")\n')
            self.assertFalse((root/'main.py').exists())

    def test_credentials_and_generated_files(self):
        self.assertEqual(content_reason(b'-----BEGIN PRIVATE KEY-----\nx'),'credential-pattern')
        self.assertEqual(path_reason('src/bundle.min.js'),'generated-or-lock-file')
        self.assertIsNone(path_reason('src/main.cpp'))


if __name__=='__main__':unittest.main()
