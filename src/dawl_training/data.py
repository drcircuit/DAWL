"""Book-separated next-token windows, preserving per-Frag provenance."""
import numpy as np
from dawl_node.common import unpack_ids
from dawl_profiles.profiles import content_id


def make_dataset(objects,entries):
    result={'schema':'hypertrix.training-dataset/1','train':[],'validation':[],'test':[],
        'split_policy':{'validation_books':[1342],'test_books':[84]},
        'window_policy':'128 next-token targets; never cross Frag; keep masked remainder; no inserted tokens'}
    for entry in entries:
        data=entry['body']['payload']['data']; book=data['book_id']
        split='validation' if book==1342 else 'test' if book==84 else 'train'
        manifest=objects.read_json(data['manifest'])
        for record in manifest['records']:
            for offset in range(0,record['count']-1,128):
                result[split].append({'book':book,'entry':entry['id'],'frag':record['index'],
                    'tokens':record['tokens'],'offset':offset,'count':min(128,record['count']-offset-1)})
    for split in ('train','validation','test'):
        result[split].sort(key=lambda r:content_id({'seed':20261014,'window':r}))
        if not result[split]: raise ValueError('empty data split')
    return result


def batch(records,objects,vocab):
    x=np.zeros((len(records),128),dtype=np.int64)
    y=np.full((len(records),128),-100,dtype=np.int64)
    for i,r in enumerate(records):
        ids=unpack_ids(objects.read(r['tokens'])); off,n=r['offset'],r['count']
        if not 1<=n<=128 or off<0 or off+n>=len(ids) or any(t>=vocab for t in ids):
            raise ValueError('invalid next-token window')
        x[i,:n]=ids[off:off+n]; y[i,:n]=ids[off+1:off+n+1]
    return x,y
