"""Bounded pull-only peer artifact and round-record exchange."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import re
import threading
import time
from urllib.error import HTTPError,URLError
from dawl_node.common import read_bytes,read_json,canonical_json,parse_json
from dawl_platform.artifacts import PublicPeer


def serve(root,host,port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            match=re.fullmatch(r'/api/objects/([0-9a-f]{64})',self.path)
            record=re.fullmatch(r'/records/(announce|update|accept|ledger)/(\d{3})',self.path)
            final=re.fullmatch(r'/(final|audit|release|release-vote)',self.path)
            if match: path=root/'objects'/match[1]; cap=256*1024**2
            elif record: path=root/record[1]/(record[2]+'.json'); cap=4*1024**2
            elif final: path=root/(final[1]+'.json'); cap=4*1024**2
            else: self.send_error(404); return
            if not path.is_file(): self.send_error(404); return
            data=read_bytes(path,cap); self.send_response(200); self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            try: self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError): pass
        def log_message(self,*args): pass
    server=ThreadingHTTPServer((host,port),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server


class Network:
    def __init__(self,objects,urls,journal=None):
        self.objects=objects; self.peers=[PublicPeer(u,True) for u in urls]
        self.bytes=0; self.requests=0; self.waits=0; self.journal=journal
        if journal and journal.exists():
            for line in journal.read_bytes().splitlines():
                self.bytes+=parse_json(line)['artifact']['bytes']; self.requests+=1
    def fetch(self,desc):
        if (self.objects.root/desc['sha256']).exists(): self.objects.read(desc); return
        errors=[]
        for peer in self.peers:
            try:
                self.objects.fetch(peer,desc); self.bytes+=desc['bytes']; self.requests+=1
                if self.journal:
                    with self.journal.open('ab') as stream: stream.write(canonical_json({'artifact':desc,'peer':peer.url})+b'\n')
                return
            except (HTTPError,URLError,TimeoutError,OSError,ValueError) as error: errors.append(type(error).__name__)
        raise ValueError('artifact unavailable or corrupt from every configured peer: '+','.join(errors))
    def record(self,url,kind,round_number,timeout=900):
        peer=PublicPeer(url,True); start=time.monotonic(); notice=0
        while time.monotonic()-start<timeout:
            try: return peer.json(f'/records/{kind}/{round_number:03d}')
            except (HTTPError,URLError,TimeoutError,OSError):
                self.waits+=1
                if time.monotonic()-notice>15:
                    print(f'Waiting for peer {kind} round {round_number}',flush=True); notice=time.monotonic()
                time.sleep(0.5)
        raise TimeoutError('round paused: required admitted peer unavailable')
