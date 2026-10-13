"""Isolated experiment proxy. Never part of the generated production deployment."""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import time
from urllib.request import Request, urlopen

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--backend",required=True)
parser.add_argument("--port",type=int,required=True)
parser.add_argument("--mode",choices=("tamper-state","corrupt-piece"),required=True)
args=parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*unused): pass
    def serve(self):
        body=self.rfile.read(int(self.headers.get("Content-Length",0))) if self.command=="POST" else None
        try:
            with urlopen(Request(args.backend+self.path,data=body,headers={"Content-Type":"application/json"}),timeout=10) as response:
                data=response.read(24*1024*1024)
            if args.mode=="tamper-state":
                result=json.loads(data)
                if body and json.loads(body).get("method")=="abci_query" and "result" in result:
                    value=result["result"]["response"]
                    state=json.loads(base64.b64decode(value["value"]))
                    state["expired_leases"]+=999
                    value["value"]=base64.b64encode(json.dumps(state,sort_keys=True,separators=(',',':')).encode()).decode()
                    data=json.dumps(result).encode()
            elif "/swarm/pieces/" in self.path:
                time.sleep(.1)
                data=bytes([data[0]^1])+data[1:]
            self.send_response(200);self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
        except Exception:
            self.send_response(503);self.end_headers()
    do_GET=serve
    do_POST=serve

ThreadingHTTPServer(("0.0.0.0",args.port),Handler).serve_forever()
