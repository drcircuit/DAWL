"""Pinned GPT-OSS metadata for the o200k ordinary-input-row compatibility test."""
import hashlib
import json
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
REPO='openai/gpt-oss-20b'
REVISION='6cee5e81ee83917806bbde320786a8fb61efebee'
FILES={
    'config.json':(1806,'3a2a26ded679375b7928ddeca59764df7cea83220c1961035f6d6e232659e9ce'),
    'tokenizer.json':(27868174,'0614fe83cadab421296e664e1f48f4261fa8fef6e03e63bb75c20f38e37d07d3'),
    'tokenizer_config.json':(4200,'9279e942392b742d633c7adbb89ebe002c98399db8926a7af5125c726f404070'),
    'model.safetensors.index.json':(36355,'0e085b977c4c9942f85938828e8c989ed7d5cdabf852e4da6a67c116cd502cd1'),
}


def main():
    root=ROOT/'artifacts/tokenizer-candidates/gpt-oss-20b';root.mkdir(parents=True,exist_ok=True)
    lock={'repo':REPO,'revision':REVISION,'files':{}}
    with httpx.Client(follow_redirects=True,timeout=90) as client:
        for name,(size,digest) in FILES.items():
            url=f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}'; path=root/name
            if path.exists(): payload=path.read_bytes()
            else:
                with client.stream('GET',url) as response:
                    response.raise_for_status(); data=bytearray()
                    for chunk in response.iter_bytes(1024*1024):
                        data.extend(chunk)
                        if len(data)>size: raise ValueError('metadata exceeds pinned size')
                payload=bytes(data)
            if len(payload)!=size or hashlib.sha256(payload).hexdigest()!=digest: raise ValueError('metadata differs from pin')
            if not path.exists(): path.write_bytes(payload)
            lock['files'][name]={'sha256':digest,'bytes':size,'url':url}
    target=root/'download-lock.json'
    if target.exists() and json.loads(target.read_bytes())!=lock: raise ValueError('metadata acquisition lock changed')
    target.write_text(json.dumps(lock,indent=2)+'\n',encoding='utf-8')
    index=json.loads((root/'model.safetensors.index.json').read_bytes())
    if index['weight_map']['model.embed_tokens.weight']!='model-00002-of-00002.safetensors':
        raise ValueError('input tensor shard differs from acquisition plan')
    print('Verified pinned GPT-OSS metadata; fetch weights with fetch_profile_weight_ranges.py gpt-oss-20b')


if __name__=='__main__': main()
