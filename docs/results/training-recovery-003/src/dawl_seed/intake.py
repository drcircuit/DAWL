"""Reproducible, inert TEI ingestion of the NB bokselskap.no 2023 distribution."""
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import xml.etree.ElementTree as ET
from dawl_node.common import immutable_write, write_json, sha256
from dawl_scratch.data import file_hash
from .progress import progress

ORIGIN = 'https://www.nb.no/sprakbanken/ressurskatalog/oai-nb-no-sbr-81/'
CATALOG = 'https://data.norge.no/en/datasets/246b8421-5aa8-3efe-ac4b-3b05fe243ef3/bokselskapno-2023'
ARCHIVE = 'https://www.nb.no/sbfil/tekst/20230119_bokselskap.no.tar.gz'
LICENSE = 'https://creativecommons.org/publicdomain/zero/1.0/'
TRANSFORM = 'tei-body-v2: all outermost XML bodies in document order; omit notes/fw/figures; prefer corr/reg/expan in choice; block/line boundaries; NFC; whitespace normalization.'


def extract(raw):
    import unicodedata
    if len(raw)>10*1024*1024 or re.search(br'<!ENTITY\s',raw,re.I):
        raise ValueError('Oversize XML or entity declaration')
    # ElementTree does not fetch external DTDs. Internal named entity declarations
    # are rejected above; only XML built-ins and numeric character references work.
    tree=ET.fromstring(raw)
    for node in tree.iter(): node.tag=node.tag.split('}')[-1]
    parents={child:parent for parent in tree.iter() for child in parent}
    def inside_body(node):
        while node in parents:
            node=parents[node]
            if node.tag=='body':return True
        return False
    bodies=[node for node in tree.iter('body') if not inside_body(node)]
    if not bodies: raise ValueError('No TEI body')
    def walk(node):
        if node.tag in ('note','fw','figure'):return ''
        if node.tag=='choice':
            preferred=next((c for tag in ('corr','reg','expan') for c in node if c.tag==tag),None)
            return walk(preferred if preferred is not None else node[0]) if len(node) else ''
        parts=[node.text or '']
        for child in node: parts.extend((walk(child), child.tail or ''))
        text=''.join(parts)
        return ('\n'+text+'\n') if node.tag in ('p','head','l','lg','sp','div','item','lb','speaker','table','row') else text
    text=unicodedata.normalize('NFC','\n\n'.join(walk(body) for body in bodies))
    text='\n'.join(re.sub(r'\s+',' ',line).strip() for line in text.splitlines())
    text=re.sub(r'\n{3,}','\n\n',text).strip()+'\n'
    if len(text.strip())<100:raise ValueError('Insufficient body text')
    def meta(tag):
        values=tree.findall('.//sourceDesc//'+tag)
        if not values:values=tree.findall('.//titleStmt/'+tag)
        return [' '.join(''.join(n.itertext()).split()) for n in values]
    return text.encode('utf-8'), {'titles':meta('title'),'authors':meta('author')}


def ingest(root):
    root=Path(root)
    if (root/'source-lock.json').exists():raise ValueError('Source lock exists; preserve this intake')
    archive=root/'bokselskap.tar.gz'
    digest=file_hash(archive)
    records=[];excluded=[]
    with tarfile.open(archive,'r:gz') as stream:
        members=stream.getmembers()
        if len(members)>4096 or sum(m.size for m in members)>250*1024*1024:raise ValueError('Archive exceeds intake bounds')
        xml=[m for m in members if m.isfile() and m.name.endswith('.xml')]
        for i,member in enumerate(xml):
            path=PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe archive member')
            raw=stream.extractfile(member).read(10*1024*1024+1)
            raw_hash=sha256(raw);immutable_write(root/'objects'/raw_hash,raw)
            try: text,meta=extract(raw)
            except (ValueError,ET.ParseError) as error:
                excluded.append({'member':member.name,'sha256':raw_hash,'reason':str(error)});continue
            text_hash=sha256(text);immutable_write(root/'texts'/text_hash,text)
            # Entire author collections remain together, including multi-volume works.
            author='|'.join(sorted(a.casefold() for a in meta['authors'])) or path.stem.split('_')[0].casefold()
            records.append({'id':'nb:bokselskap-2023:'+path.stem,'group':'nb:author:'+author,
                'kind':'tei','domain':'norwegian','text_sha256':text_hash,'source_sha256':raw_hash,
                'origin':{'publisher':'Nasjonalbiblioteket / Språkbanken','source_url':ORIGIN,'catalog_url':CATALOG,
                    'distribution_url':ARCHIVE,'release':'2023-01-19','archive_sha256':digest,'archive_member':member.name,
                    'license':'CC0-1.0','license_url':LICENSE,'license_basis':'Declared distribution licence in both publisher and data.norge.no catalogue',
                    'transformation':TRANSFORM,**meta}})
            progress(root,'Extracting Norwegian texts',job='Corpus intake',role='tokenizer',status='running',
                     completed=i+1,total=len(xml),unit='documents',documents=len(records),source=path.stem)
    write_json(root/'source-lock.json',{'schema':'hypertrix.tei-source-lock/1','archive_sha256':digest,
        'extractor_sha256':file_hash(Path(__file__)),'sources':records,'exclusions':excluded,
        'catalogue_snapshots':json.loads((root/'downloads.json').read_bytes()) if (root/'downloads.json').exists() else []})
    progress(root,'Intake complete',job='Corpus intake',role='tokenizer',status='complete',
             completed=len(xml),total=len(xml),unit='documents',documents=len(records),excluded=len(excluded))
    return {'admitted':len(records),'excluded':len(excluded),'archive_sha256':digest}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    print(json.dumps(ingest(p.parse_args().root)))
