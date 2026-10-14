"""Separate, review-only Azure site. Contains no response API or participant database."""
import argparse
import hashlib
import json
import mimetypes
import sys
from control import REPO,az,save
sys.path.insert(0,str(REPO/'scripts'))
from build_study_review import build
from public_site import SignedInCredential
from azure.storage.blob import BlobServiceClient,ContentSettings,StaticWebsite

SUBSCRIPTION='58eadcc1-71bf-4547-bd13-7f68a6042a0f'
ACCOUNT='hypertrixstudy1014'
GROUP='DAWL'
RESOURCE=f'/subscriptions/{SUBSCRIPTION}/resourceGroups/{GROUP}/providers/Microsoft.Storage/storageAccounts/{ACCOUNT}'
ROOT=REPO/'artifacts/study-review'


def checked_account():
    a=az('storage','account','show','--subscription',SUBSCRIPTION,'-g',GROUP,'-n',ACCOUNT)
    if a['id'].lower()!=RESOURCE.lower() or a['location']!='norwayeast' or a.get('tags',{}).get('purpose')!='study-review':
        raise ValueError('Study resource scope differs')
    return a


def setup():
    existing=az('storage','account','list','--subscription',SUBSCRIPTION,'-g',GROUP)
    if not any(a['name']==ACCOUNT for a in existing):
        az('storage','account','create','--subscription',SUBSCRIPTION,'-g',GROUP,'-n',ACCOUNT,
           '--location','norwayeast','--sku','Standard_LRS','--kind','StorageV2',
           '--https-only','true','--min-tls-version','TLS1_2','--allow-blob-public-access','false',
           '--tags','project=hypertrix','purpose=study-review','dataCollection=disabled')
    a=checked_account()
    keys=az('storage','account','keys','list','--subscription',SUBSCRIPTION,'-g',GROUP,'-n',ACCOUNT)
    with BlobServiceClient(a['primaryEndpoints']['blob'],credential=keys[0]['value']) as client:
        client.set_service_properties(static_website=StaticWebsite(enabled=True,index_document='index.html',error_document404_path='404.html'))
    del keys
    user=az('ad','signed-in-user','show','--query','id')
    scope=RESOURCE+'/blobServices/default/containers/$web'
    assignments=az('role','assignment','list','--subscription',SUBSCRIPTION,'--scope',scope)
    if not any(r.get('principalId')==user and r.get('roleDefinitionName')=='Storage Blob Data Contributor' for r in assignments):
        az('role','assignment','create','--subscription',SUBSCRIPTION,'--assignee-object-id',user,
           '--assignee-principal-type','User','--role','Storage Blob Data Contributor','--scope',scope)
    a=checked_account()
    state={'resource_group':GROUP,'account':ACCOUNT,'region':a['location'],'url':a['primaryEndpoints']['web'],
           'mode':'review-only','response_collection':False,'platform_integration':False}
    save(ROOT/'deployment.json',state);print(json.dumps(state))


def deploy():
    a=checked_account();manifest=build();root=REPO/'output/study-review'
    for name,expected in manifest['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Unapproved study file')
    with BlobServiceClient(a['primaryEndpoints']['blob'],credential=SignedInCredential()) as client:
        container=client.get_container_client('$web')
        for name in [n for n in manifest['files'] if n!='index.html']+['manifest.json','index.html']:
            kind=mimetypes.guess_type(name)[0] or 'application/octet-stream'
            if name.endswith('.js'):kind='text/javascript'
            container.upload_blob(name,(root/name).read_bytes(),overwrite=True,
                content_settings=ContentSettings(content_type=kind,cache_control='no-cache'))
    print(json.dumps({'published_files':len(manifest['files'])+1,'url':a['primaryEndpoints']['web']}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['setup','deploy']);args=p.parse_args()
    setup() if args.command=='setup' else deploy()
