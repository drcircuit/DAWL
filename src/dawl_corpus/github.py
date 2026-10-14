"""Public GitHub snapshots as inert, content-addressed training candidates.

Nothing from a repository is imported or executed. Original archives stay outside
the project checkout. Admission and exclusions are recorded separately from bytes.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import urllib.request
import urllib.parse
import zipfile

from dawl_node.common import canonical_json, immutable_write, sha256, write_json

EXTENSIONS = set('py pyi js mjs cjs jsx ts tsx cs fs vb c cc cpp cxx h hh hpp java kt kts go rs rb php swift scala lua r jl sh bash ps1 psm1 bat cmd asm s inc glsl vert frag geom hlsl fx wgsl sql html htm css scss sass less vue svelte md markdown rst txt json yaml yml toml xml xaml csproj fsproj props targets cmake make gradle dockerfile gitignore'.split())
SKIP_DIRS = {'node_modules', 'vendor', 'vendors', 'third_party', 'third-party', 'external', 'packages', '.git', '.svn', 'bin', 'obj', 'dist', 'build', 'coverage', '__pycache__', '.next', '.venv', 'venv'}
LOCK_FILES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'composer.lock', 'cargo.lock', 'poetry.lock', 'uv.lock'}
POLICY = {'schema': 'hypertrix.github-admission/1', 'extensions': sorted(EXTENSIONS),
          'excluded_directories': sorted(SKIP_DIRS), 'max_file_bytes': 2 * 1024 * 1024,
          'max_archive_bytes': 256 * 1024 * 1024, 'max_uncompressed_bytes': 1024 ** 3,
          'scope': 'Local research candidates; no public redistribution grant inferred.',
          'owner_permission': 'User authorized use of source code in their public drcircuit repositories on 2026-10-14.',
          'fork_rule': 'Unknown or unrecognized license requires review; repository license metadata is not a file-level legal determination.',
          'normalization': 'None; original UTF-8 bytes, exact SHA256 deduplication.'}
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|AKIA[0-9A-Z]{16})\b')


def api(path):
    request = urllib.request.Request('https://api.github.com/' + path,
        headers={'User-Agent': 'Hypertrix-corpus/1', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.load(response)


def inventory(owner, root):
    if not re.fullmatch(r'[A-Za-z0-9-]{1,39}', owner):
        raise ValueError('Invalid public GitHub owner')
    path = root / 'inventory.json'
    if path.exists():
        result = json.loads(path.read_bytes())
        if result['owner'] != owner: raise ValueError('Inventory owner differs')
        return result
    repos = []
    for page in range(1, 101):
        batch = api(f'users/{owner}/repos?type=owner&per_page=100&sort=full_name&page={page}')
        for repo in batch:
            if repo['private'] or repo['owner']['login'].lower() != owner.lower():
                raise ValueError('Non-public or unexpected repository')
            repos.append({key: repo[key] for key in ('id', 'full_name', 'default_branch', 'fork', 'archived', 'size', 'language', 'html_url')} |
                         {'license': (repo.get('license') or {}).get('spdx_id')})
        if len(batch) < 100: break
    else: raise ValueError('Repository pagination limit exceeded')
    result = {'schema': 'hypertrix.github-inventory/1', 'owner': owner,
              'retrieved_at': datetime.now(timezone.utc).isoformat(), 'repositories': repos}
    write_json(path, result)
    write_json(root / 'policy.json', POLICY)
    return result


def path_reason(path):
    parts = PurePosixPath(path).parts
    if not parts or path.startswith('/') or '\\' in path or any(p in ('.', '..') for p in parts):
        return 'unsafe-path'
    lower = [p.lower() for p in parts]
    name = lower[-1]
    if any(p in SKIP_DIRS for p in lower[:-1]): return 'dependency-or-generated-directory'
    if name.startswith('.env') or name in ('credentials', 'id_rsa', 'id_ed25519') or name.endswith(('.pem', '.pfx', '.p12', '.key')):
        return 'credential-path'
    if name in LOCK_FILES or name.endswith(('.min.js', '.min.css', '.map')): return 'generated-or-lock-file'
    if name.startswith(('license', 'copying', 'notice')): return 'license-evidence'
    suffix = name.rsplit('.', 1)[-1]
    if suffix not in EXTENSIONS and name not in ('dockerfile', 'makefile', 'cmakelists.txt'):
        return 'non-source-format'
    return None


def content_reason(payload):
    if not payload: return 'empty'
    if len(payload) > POLICY['max_file_bytes']: return 'file-size-limit'
    if b'\x00' in payload: return 'binary'
    if SECRET.search(payload): return 'credential-pattern'
    try: text = payload.decode('utf-8')
    except UnicodeDecodeError: return 'non-utf8'
    if max(map(len, text.splitlines()), default=0) > 12000: return 'minified-or-generated-line'
    return None


def inspect_archive(archive, root, repo, commit):
    files, exclusions, licenses, seen = [], [], [], set()
    with zipfile.ZipFile(archive) as z:
        if sum(i.file_size for i in z.infolist()) > POLICY['max_uncompressed_bytes']:
            raise ValueError('Uncompressed archive limit exceeded')
        for item in sorted(z.infolist(), key=lambda i: i.filename):
            if item.is_dir(): continue
            _, separator, path = item.filename.partition('/')
            if not separator or path in seen: raise ValueError('Invalid or duplicate archive path')
            seen.add(path)
            reason = path_reason(path)
            if stat.S_ISLNK(item.external_attr >> 16): reason = 'symlink'
            if item.file_size > POLICY['max_file_bytes']: reason = 'file-size-limit'
            if reason and reason != 'license-evidence':
                exclusions.append({'path': path, 'reason': reason, 'bytes': item.file_size}); continue
            payload = z.read(item)
            content_flag = content_reason(payload)
            if content_flag:
                exclusions.append({'path': path, 'reason': content_flag, 'bytes': len(payload)}); continue
            digest = sha256(payload)
            immutable_write(root / 'objects' / digest, payload)
            entry = {'path': path, 'sha256': digest, 'bytes': len(payload),
                     'source_url': f'https://github.com/{repo["full_name"]}/blob/{commit}/{urllib.parse.quote(path)}'}
            if reason == 'license-evidence': licenses.append(entry)
            else: files.append(entry)
    return files, exclusions, licenses


def fetch_repo(repo, root):
    name = repo['full_name'].split('/')[1]
    target = root / 'repositories' / name
    target.mkdir(parents=True, exist_ok=True)
    result_path = target / 'snapshot.json'
    if result_path.exists(): return json.loads(result_path.read_bytes())
    pin_path = target / 'pin.json'
    if pin_path.exists(): pin = json.loads(pin_path.read_bytes())
    else:
        output = subprocess.run(['git', '-c', 'credential.helper=', 'ls-remote',
            'https://github.com/' + repo['full_name'] + '.git', 'refs/heads/' + repo['default_branch']],
            capture_output=True, text=True, timeout=90, check=True).stdout.strip()
        if not output:
            result = {**repo, 'status': 'empty-repository', 'files': [], 'exclusions': [], 'licenses': []}
            write_json(result_path, result); return result
        commit = output.split()[0]
        if not re.fullmatch('[a-f0-9]{40}', commit): raise ValueError('Invalid commit')
        family = repo['full_name']
        if repo['fork']:
            details = api('repos/' + repo['full_name'])
            family = details.get('source', details.get('parent', repo))['full_name']
        pin = {'commit': commit, 'family': family}
        write_json(pin_path, pin)
    commit = pin['commit']
    archive = target / (commit + '.zip')
    if not archive.exists() and (target/'source-only.git').exists():
        return fetch_git_sources(repo, root, target, pin)
    if not archive.exists():
        partial = target / (commit + '.partial')
        request = urllib.request.Request(f'https://codeload.github.com/{repo["full_name"]}/zip/{commit}',
                                         headers={'User-Agent': 'Hypertrix-corpus/1'})
        with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as out:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > POLICY['max_archive_bytes']: break
                out.write(chunk)
        if count > POLICY['max_archive_bytes']:
            return fetch_git_sources(repo, root, target, pin)
        partial.replace(archive)
    files, exclusions, licenses = inspect_archive(archive, root, repo, commit)
    with archive.open('rb') as stream: archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    admitted = not repo['fork'] or repo['license'] not in (None, 'NOASSERTION')
    result = {**repo, **pin, 'schema': 'hypertrix.github-snapshot/1', 'archive_sha256': archive_hash,
              'status': 'candidate' if admitted else 'rights-review', 'files': files,
              'licenses': licenses, 'exclusions': exclusions,
              'rights_basis': 'repository-license-metadata' if repo['fork'] else 'owner-authorized-local-experiment',
              'policy_sha256': sha256(canonical_json(POLICY))}
    write_json(result_path, result)
    return result


def fetch_git_sources(repo, root, target, pin):
    """Large media-heavy repositories: fetch Git trees and selected source blobs only."""
    bare = target/'source-only.git'
    if not bare.exists():
        subprocess.run(['git','-c','credential.helper=','clone','--bare','--depth=1','--filter=blob:none',
                        'https://github.com/'+repo['full_name']+'.git',str(bare)],check=True,capture_output=True,timeout=180)
    command = ['git','-c','credential.helper=','--git-dir='+str(bare)]
    if subprocess.run(command+['cat-file','-e',pin['commit']+'^{commit}'],capture_output=True).returncode:
        subprocess.run(command+['fetch','--depth=1','--filter=blob:none','origin',pin['commit']],check=True,capture_output=True,timeout=180)
    listing = subprocess.check_output(command+['ls-tree','-rz',pin['commit']],timeout=90)
    selected, exclusions = [], []
    for row in listing.split(b'\0'):
        if not row: continue
        metadata, path = row.split(b'\t',1)
        mode,kind,object_id=metadata.decode('ascii').split()
        path=path.decode('utf-8'); reason=path_reason(path)
        if kind!='blob' or mode not in ('100644','100755'):reason='non-regular-git-entry'
        if reason and reason!='license-evidence':
            exclusions.append({'path':path,'reason':reason});continue
        selected.append((path,object_id,reason))
    # Fetch bounded-size blobs in a single pack. Only selected source entries are
    # admitted below; small media may exist in Git's cache but never in the corpus.
    subprocess.run(command+['fetch','--refetch','--filter=blob:limit='+str(POLICY['max_file_bytes']),
        '--depth=1','--no-tags','--no-write-fetch-head','origin',pin['commit']],
        check=True,capture_output=True,timeout=300)
    files,licenses=[],[]
    process=subprocess.Popen(command+['cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for path,object_id,reason in selected:
            process.stdin.write((object_id+'\n').encode());process.stdin.flush()
            header=process.stdout.readline().decode('ascii').split()
            if len(header)!=3 or header[1]!='blob':raise ValueError('Unexpected Git object')
            size=int(header[2]);payload=process.stdout.read(size)
            if process.stdout.read(1)!=b'\n' or len(payload)!=size:raise ValueError('Truncated Git object')
            if hashlib.sha1(b'blob '+str(size).encode()+b'\0'+payload).hexdigest()!=object_id:
                raise ValueError('Git object hash mismatch')
            flag=content_reason(payload)
            if flag:exclusions.append({'path':path,'reason':flag,'bytes':size});continue
            digest=sha256(payload);immutable_write(root/'objects'/digest,payload)
            item={'path':path,'sha256':digest,'git_blob':object_id,'bytes':size,
                  'source_url':f'https://github.com/{repo["full_name"]}/blob/{pin["commit"]}/{urllib.parse.quote(path)}'}
            (licenses if reason=='license-evidence' else files).append(item)
    finally:
        process.stdin.close();process.wait(timeout=30)
    admitted=not repo['fork'] or repo['license'] not in (None,'NOASSERTION')
    result={**repo,**pin,'schema':'hypertrix.github-snapshot/1','transport':'git-partial-clone-source-blobs',
            'git_tree_listing_sha256':sha256(listing),'status':'candidate' if admitted else 'rights-review',
            'files':files,'exclusions':exclusions,'licenses':licenses,
            'rights_basis':'repository-license-metadata' if repo['fork'] else 'owner-authorized-local-experiment',
            'policy_sha256':sha256(canonical_json(POLICY))}
    write_json(target/'snapshot.json',result)
    return result


def fetch_all(owner, root, workers=4):
    root.mkdir(parents=True, exist_ok=True)
    listing = inventory(owner, root)
    if json.loads((root/'policy.json').read_bytes()) != POLICY:
        raise ValueError('Policy changed; create a new corpus root')
    def one(repo):
        try:
            result = fetch_repo(repo, root)
            print(json.dumps({'repository': repo['full_name'], 'status': result['status'], 'files': len(result['files'])}), flush=True)
            return result
        except Exception as error:
            # Exception text can contain remote content. Persist class only, no source bytes.
            print(json.dumps({'repository': repo['full_name'], 'status': 'fetch-error', 'error': type(error).__name__}), flush=True)
            return {**repo, 'status': 'fetch-error', 'error': type(error).__name__, 'files': []}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, listing['repositories']))
    report = {'schema': 'hypertrix.github-corpus/1', 'inventory_sha256': sha256((root/'inventory.json').read_bytes()),
              'policy_sha256': sha256(canonical_json(POLICY)), 'statuses': dict(Counter(r['status'] for r in results)),
              'repositories': [{k: v for k, v in r.items() if k not in ('files', 'exclusions', 'licenses')} |
                   {'file_count': len(r['files']), 'source_bytes': sum(f['bytes'] for f in r['files'])} for r in results]}
    # Progress is replaceable; completed source locks above are immutable.
    (root/'progress.json').write_bytes(canonical_json(report))
    if not any(r['status'] == 'fetch-error' for r in results): write_json(root/'corpus-lock.json', report)
    return report
