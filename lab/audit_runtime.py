"""Report accidental credentials in app/home files without printing their values."""
import json
import os
from pathlib import Path
import re
pattern=re.compile(rb'sk-proj-[A-Za-z0-9_-]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
hits=[];count=0
for location in ('/app','/root','/home'):
    for path in Path(location).rglob('*'):
        if path.is_file() and not path.is_symlink() and path.stat().st_size<5_000_000 and path.suffix in ('.py','.sh','.json','.pem','.env','.txt'):
            count+=1
            if pattern.search(path.read_bytes()):hits.append(str(path))
env=[k for k in os.environ if re.search(r'TOKEN|PASSWORD|SECRET|API_KEY',k) and k!='TOKENIZERS_PARALLELISM']
print(json.dumps({'files_scanned':count,'sensitive_content_paths':hits,'suspicious_environment_names':env}))
if hits or env:raise SystemExit(1)
