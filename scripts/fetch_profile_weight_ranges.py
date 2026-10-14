"""Resumable bounded-range transport for slow checkpoint CDN streams.

Transport pieces are not trusted hashes. The assembled file must match the full
pinned upstream SHA before publication or import. Public unauthenticated URLs.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import httpx
from fetch_profile_weights import ROOT, FILES, fetch

PIECE = 8*1024*1024


def download(name):
    filename,size,digest,_=FILES[name]; folder=ROOT/'artifacts/profile-weights'/name
    folder.mkdir(parents=True,exist_ok=True); target=folder/filename
    if target.exists(): fetch(name); return
    parts=folder/(filename+'.parts'); parts.mkdir(exist_ok=True)
    lock=json.loads((ROOT/'artifacts/tokenizer-candidates'/name/'download-lock.json').read_bytes())
    url=f"https://huggingface.co/{lock['repo']}/resolve/{lock['revision']}/{filename}"
    def part(start):
        end=min(size,start+PIECE)-1; path=parts/str(start)
        if path.exists() and path.stat().st_size==end-start+1: return
        for attempt in range(4):
            try:
                before=time.monotonic(); payload=bytearray()
                with httpx.stream('GET',url+f'?download=true&range_start={start}',
                        headers={'Range':f'bytes={start}-{end}'},follow_redirects=True,timeout=30) as response:
                    response.raise_for_status()
                    if response.status_code!=206 or response.headers.get('content-range')!=f'bytes {start}-{end}/{size}':
                        raise ValueError('server did not honor exact bounded range')
                    for data in response.iter_bytes(256*1024):
                        payload.extend(data)
                        if len(payload)>end-start+1 or time.monotonic()-before>90:
                            raise ValueError('range transfer exceeded bound')
                if len(payload)!=end-start+1: raise ValueError('incomplete range')
                temporary=path.with_suffix('.pending'); temporary.write_bytes(payload); temporary.replace(path)
                return
            except (httpx.HTTPError,ValueError):
                if attempt==3: raise
                time.sleep(1+attempt)
    starts=range(0,size,PIECE); done=0; before=time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in pool.map(part,starts):
            done+=1
            if done%32==0: print(f'{name}: {done}/{len(starts)} ranges verified for length',flush=True)
    sha=hashlib.sha256(); temporary=folder/(filename+'.assembling')
    with temporary.open('wb') as output:
        for start in starts:
            data=(parts/str(start)).read_bytes(); sha.update(data); output.write(data)
    if temporary.stat().st_size!=size or sha.hexdigest()!=digest: raise ValueError('assembled checkpoint SHA mismatch')
    temporary.rename(target)
    fetch(name)
    print(f'{name}: acquisition completed in {time.perf_counter()-before:.2f}s',flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('names',nargs='*',help='Profile weight names: '+', '.join(FILES))
    args=parser.parse_args()
    if any(name not in FILES for name in args.names): parser.error('unknown profile weight name')
    for name in args.names or ['smollm2','qwen3','qwen35','smollm3']: download(name)
