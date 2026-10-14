"""Transport-only container. It never mounts node keys or runs DAWL node roles."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from hybrid_topology import endpoints


def main():
    deadline=datetime.fromisoformat(os.environ['DAWL_DEADLINE'])
    if not 0<(deadline-datetime.now(timezone.utc)).total_seconds()<=86400: raise ValueError('expired deadline')
    exchange=Path('/exchange');exchange.mkdir(exist_ok=True);os.chown(exchange,1000,1000)
    ssh=Path('/run/dawl-ssh');ssh.mkdir(parents=True,exist_ok=True);Path('/run/sshd').mkdir(exist_ok=True)
    subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(ssh/'host')],check=True)
    key=os.environ['DAWL_OPERATOR_PUBLIC_KEY'].strip()
    nodes=json.loads(os.environ['DAWL_NODES'])
    discovery=os.environ.get('DAWL_DHT')=='1'
    if not key.startswith('ssh-ed25519 ') or '\n' in key: raise ValueError('invalid operator public key')
    auth=Path('/home/dawl/.ssh');auth.mkdir(mode=0o700,exist_ok=True);os.chown(auth,1000,1000)
    (auth/'authorized_keys').write_text(key+'\n');os.chmod(auth/'authorized_keys',0o600);os.chown(auth/'authorized_keys',1000,1000)
    config='\n'.join(['Port 2222','ListenAddress 0.0.0.0','HostKey '+str(ssh/'host'),'PidFile '+str(ssh/'pid'),
        'PasswordAuthentication no','KbdInteractiveAuthentication no','PermitEmptyPasswords no','PermitRootLogin no','UsePAM no',
        'AllowUsers dawl','AllowAgentForwarding no','X11Forwarding no','GatewayPorts clientspecified','AllowTcpForwarding yes',
        'PermitOpen '+' '.join(f'{h}:{p}' for h,p in endpoints('cloud',nodes,discovery)),
        'PermitListen '+' '.join(f'{h}:{p}' for h,p in endpoints('local',nodes,discovery)),
        'Subsystem sftp internal-sftp','LogLevel ERROR'])+'\n'
    (ssh/'config').write_text(config)
    subprocess.run(['/usr/sbin/sshd','-t','-f',str(ssh/'config')],check=True)
    child=subprocess.Popen(['/usr/sbin/sshd','-D','-e','-f',str(ssh/'config')])
    class Telemetry(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/health','/status'): self.send_error(404);return
            value={'role':'transport-gateway','nodes':[]}
            if self.path=='/status':
                for node in nodes:
                    path=exchange/'status'/(node['name']+'.json')
                    if path.exists() and path.stat().st_size<100000:
                        try: value['nodes'].append(json.loads(path.read_text()))
                        except (OSError,ValueError): pass
            payload=json.dumps(value).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*_): pass
    server=ThreadingHTTPServer(('127.70.0.31',8901),Telemetry)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    stopping=False
    def stop(*_):
        nonlocal stopping
        stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print('DAWL_SSH_HOST_KEY '+(ssh/'host.pub').read_text().strip(),flush=True)
    try:
        while not stopping and datetime.now(timezone.utc)<deadline and not (exchange/'gateway.stop').exists():
            if child.poll() is not None: raise RuntimeError('SSH service exited')
            time.sleep(1)
    finally:
        server.shutdown()
        child.terminate();child.wait(timeout=10)


if __name__=='__main__': main()
