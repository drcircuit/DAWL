"""Closed-state restoration cannot silently reset a validator identity."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from runtime_upgrade import restore_closed,verify_upgrade
from dawl_node.common import keygen,load_key,write_json,sign


class RuntimeTests(unittest.TestCase):
    def test_exact_closed_archive_required_and_existing_state_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);archive=base/'private-state.tar';root=base/'restored'
            with tarfile.open(archive,'w') as tar:
                member=tarfile.TarInfo('key.private.pem');member.size=8;tar.addfile(member,io.BytesIO(b'original'))
            digest=hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):restore_closed(root,base,digest)
            (base/'started.json').touch();(base/'stopped.json').touch()
            with self.assertRaises(ValueError):restore_closed(root,base,'0'*64)
            restore_closed(root,base,digest)
            self.assertEqual((root/'key.private.pem').read_bytes(),b'original')
            restore_closed(root,base,'0'*64)
            self.assertEqual((root/'key.private.pem').read_bytes(),b'original')

    def test_archive_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);archive=base/'private-state.tar'
            (base/'started.json').touch();(base/'stopped.json').touch()
            with tarfile.open(archive,'w') as tar:
                member=tarfile.TarInfo('../escape');member.size=1;tar.addfile(member,io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError,'unsafe'):
                restore_closed(base/'state',base,hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertFalse((base/'escape').exists())

    def test_upgrade_requires_original_publisher_and_exact_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);public=keygen(base/'key',base/'public');key=load_key(base/'key')
            plan={'actors':{public:{'hats':['PUBLISHER']}}}
            body=dict(schema='hypertrix.runtime-upgrade/1',network_sha256='a'*64,role_images={'worker':'image@digest'},implementation_sha256='b'*64,deadline='tomorrow')
            write_json(base/'upgrade',sign(body,key))
            verify_upgrade(base/'upgrade','a'*64,plan,'worker','image@digest','b'*64,'tomorrow')
            for changed in [('worker','image@evil','b'*64,'tomorrow'),('worker','image@digest','c'*64,'tomorrow'),('worker','image@digest','b'*64,'later')]:
                with self.assertRaises(ValueError):verify_upgrade(base/'upgrade','a'*64,plan,*changed)
