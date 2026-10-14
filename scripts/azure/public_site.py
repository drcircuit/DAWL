"""Publish an allowlisted static observatory and verified local ledger snapshots.

Setup uses an account key only in memory for enabling static hosting. Ongoing
uploads use the signed-in Entra identity, scoped to the $web container. No keys
are written to configuration, command lines, logs or the website.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from control import REPO,az,save
sys.path.insert(0,str(REPO/'scripts'))
from public_snapshot import capture
from azure.core.credentials import AccessToken
from azure.storage.blob import BlobServiceClient,ContentSettings,StaticWebsite

SUBSCRIPTION='58eadcc1-71bf-4547-bd13-7f68a6042a0f'
ACCOUNT='dawlh1013185918state'
RESOURCE=f'/subscriptions/{SUBSCRIPTION}/resourceGroups/DAWL/providers/Microsoft.Storage/storageAccounts/{ACCOUNT}'
ROOT=REPO/'artifacts/public-site'


class SignedInCredential:
    def __init__(self):self.cached=None
    def get_token(self,*scopes,**kwargs):
        if self.cached is None or self.cached.expires_on<time.time()+120:
            v=az('account','get-access-token','--subscription',SUBSCRIPTION,'--resource','https://storage.azure.com/')
            self.cached=AccessToken(v['accessToken'],int(v['expires_on']))
        return self.cached


def service(credential=None):
    return BlobServiceClient(f'https://{ACCOUNT}.blob.core.windows.net',credential=credential or SignedInCredential())


def upload(container,name,data,content_type=None):
    container.upload_blob(name,data,overwrite=True,content_settings=ContentSettings(
        content_type=content_type or mimetypes.guess_type(name)[0] or 'application/octet-stream',cache_control='no-store' if name.startswith('live/') else 'public, max-age=300'))


def setup():
    value=az('storage','account','show','--subscription',SUBSCRIPTION,'-g','DAWL','-n',ACCOUNT)
    if value['id'].lower()!=RESOURCE.lower() or value['location']!='norwayeast':raise ValueError('Storage scope differs')
    keys=az('storage','account','keys','list','--subscription',SUBSCRIPTION,'-g','DAWL','-n',ACCOUNT)
    with service(keys[0]['value']) as client:
        client.set_service_properties(static_website=StaticWebsite(enabled=True,index_document='index.html',error_document404_path='404.html'))
    del keys
    user=az('ad','signed-in-user','show','--query','id')
    scope=RESOURCE+'/blobServices/default/containers/$web'
    az('role','assignment','create','--subscription',SUBSCRIPTION,'--assignee-object-id',user,
       '--assignee-principal-type','User','--role','Storage Blob Data Contributor','--scope',scope)
    config={'account':ACCOUNT,'resource_group':'DAWL','region':'norwayeast','url':value['primaryEndpoints']['web'],
            'mode':'read-only cloud replica; local compute','source_chain':'local protocol-4, separate from stopped hybrid chain',
            'public_container':'$web','publisher_interval_seconds':120,'private_archive_access_changed':False}
    save(ROOT/'deployment.json',config);print(json.dumps(config),flush=True)


def deploy(container):
    # Rebuild and verify this checkout; never deploy an old manifest after a failed build.
    from build_public_site import build
    build()
    root=REPO/'output/public-site';manifest=json.loads((root/'manifest.json').read_text())
    for name,digest in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Unapproved site file')
    # Do not enumerate arbitrary workspace or output files for upload.
    for name in [n for n in manifest['files'] if n!='index.html']+['manifest.json','index.html']:
        upload(container,name,(root/name).read_bytes())
    print('Published '+str(len(manifest['files'])+1)+' allowlisted site files.',flush=True)


def push(container):
    previous=None
    try:previous=json.loads(container.download_blob('live/status.json').readall())
    except Exception as e:
        from azure.core.exceptions import ResourceNotFoundError
        if not isinstance(e,ResourceNotFoundError):raise
    current,certificate,genesis=capture(ROOT,previous)
    # Immutable payload first, single status pointer last: interrupted uploads cannot
    # make the UI pair a new head with an older certificate.
    name=f"live/certificates/{current['height']:012d}-{current['certificate_sha256']}.json"
    current['certificate']=name
    upload(container,name,certificate,'application/json')
    upload(container,'live/genesis.json',genesis,'application/json')
    payload=json.dumps(current,ensure_ascii=False,separators=(',',':')).encode()
    upload(container,'live/status.json',payload,'application/json')
    save(ROOT/'last-published.json',current)
    # Keep 32 recent certificates; full historical experiment evidence is separate.
    old=sorted(b.name for b in container.list_blobs(name_starts_with='live/certificates/'))
    for candidate in old[:-32]:
        leaf=candidate.rsplit('/',1)[-1]
        if len(leaf)==82 and leaf[:12].isdigit() and leaf[12]=='-' and leaf.endswith('.json'):
            container.delete_blob(candidate)
    print(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'published_height':current['height'],'quorum_verified':True}),flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['setup','deploy','push','watch']);a=p.parse_args();ROOT.mkdir(parents=True,exist_ok=True)
    if a.command=='setup':setup();return
    with service() as client:
        container=client.get_container_client('$web')
        if a.command=='deploy':deploy(container);return
        if a.command=='push':push(container);return
        # One publisher per local workspace. File handle is held for process lifetime.
        import msvcrt
        with (ROOT/'publisher.lock').open('a+b') as fence:
            fence.seek(0);fence.write(b'1');fence.flush();fence.seek(0);msvcrt.locking(fence.fileno(),msvcrt.LK_NBLCK,1)
            while True:
                try:push(container)
                except Exception as e:
                    # Do not publish failed/unverified candidates or log credentials.
                    print(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'publication':'withheld','reason_type':type(e).__name__}),flush=True)
                time.sleep(120)


if __name__=='__main__':main()
