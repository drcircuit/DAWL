"""Explicit operator-authorized runtime upgrade; genesis and old recipe stay immutable."""
import hashlib
from pathlib import Path
import tarfile
from dawl_node.common import read_json,verify_signature


def verify_upgrade(path,network,plan,role,image,implementation,deadline):
    envelope=read_json(path)
    publishers=[p for p,a in plan['actors'].items() if 'PUBLISHER' in a['hats']]
    if envelope['body']['signer'] not in publishers:raise ValueError('runtime upgrade requires existing publisher signature')
    body=verify_signature(envelope,envelope['body']['signer'])
    if (body['schema']!='hypertrix.runtime-upgrade/1' or body['network_sha256']!=network
        or body['role_images'][role]!=image or body['implementation_sha256']!=implementation or body['deadline']!=deadline):
        raise ValueError('runtime upgrade differs from operator-approved pins')
    return body


def restore_closed(root,persist,expected):
    """Only an exact operator-exported closed archive may restore a lost private volume."""
    root=Path(root);persist=Path(persist)
    if (root/'key.private.pem').exists():return
    if not (persist/'started.json').exists():raise ValueError('restore requires original signer fence')
    if not (persist/'stopped.json').exists():raise ValueError('restore requires graceful closure')
    archive=persist/'private-state.tar'
    with archive.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=expected:raise ValueError('archive differs from operator-exported closed state')
    root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):raise ValueError('partial private state requires manual inspection')
    with tarfile.open(archive) as tar:
        for member in tar:
            destination=(root/member.name).resolve()
            if not member.isfile() or not destination.is_relative_to(root.resolve()):raise ValueError('unsafe archive member')
        tar.extractall(root,filter='data')
