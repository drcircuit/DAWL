"""Verify an extracted kit before copying it to native Linux storage."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import sys

source=Path(sys.argv[1]).resolve();storage=Path(sys.argv[2]).resolve()
raw=(source/'release-manifest.json').read_bytes();manifest=json.loads(raw)
identity=hashlib.sha256(raw).hexdigest();target=storage/'kits'/identity
for entry in manifest['files']:
    name=entry['path'];parts=PurePosixPath(name)
    if parts.is_absolute() or '\\' in name or ':' in name or any(p in ('','.','..') for p in name.split('/')):raise ValueError('Unsafe release path')
    original=source.joinpath(*parts.parts)
    if original.is_symlink() or not original.resolve().is_relative_to(source):raise ValueError('Release symlink rejected')
    if hashlib.file_digest(original.open('rb'),'sha256').hexdigest()!=entry['sha256']:raise ValueError('Release checksum differs: '+name)
    output=target.joinpath(*parts.parts)
    if output.exists():
        if output.is_symlink() or hashlib.file_digest(output.open('rb'),'sha256').hexdigest()!=entry['sha256']:raise ValueError('Existing kit changed')
    else:
        output.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,output)
(target/'release-manifest.json').write_bytes(raw)
print(target)
