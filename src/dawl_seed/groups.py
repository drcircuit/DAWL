"""Reviewed author aliases and connected source families for the 2023 NB release."""
import re
import unicodedata
from dawl_node.common import sha256

ALIASES={
    'a o vinje':'aasmund olavsson vinje','ao vinje':'aasmund olavsson vinje',
    'kristian elster de':'kristian elster','mauritz hansen':'maurits christopher hansen',
    'stein riverton':'sven elvestad','helene dickmar':'hanna butenschøn',
    'helene dickmar hanna butenschøn':'hanna butenschøn',
    'peter chr asbjørnsen':'peter christen asbjørnsen','asbjørnsen':'peter christen asbjørnsen','moe':'jørgen moe'}
QUARANTINE={'BS_PoetiskeSamlinger_tekst.xml','NBkilder_FortidensStemmer.xml',
            'NBkilder_Grunnlovskilder_tekst.xml','NSL_Skiturfortellinger_tekst.xml'}
FALLBACK={
    'BS_Njaala_tekst.xml':['anonymous-work:njaala'],
    'NB_Collett_Brev_til_Ibsen_tekst.xml':['camilla collett'],
    'NSL_Anonym_Mesteren_tekst.xml':['anonymous-work:mesteren-fra-møre'],
    'NSL_Bergsbok_tekst.xml':['anonymous-work:bergsbok'],
    'NSL_Hamarkroniken_tekst.xml':['anonymous-work:hamarkroniken'],
    'NSL_Lie_Hvaljag_Finmarksvidden_samlet.xml':['jonas lie'],
    'NSL_Pavels_1814_tekst.xml':['claus pavels'],
    'NSL_Thoresen_Brandes_Brevveksling_tekst.xml':['magdalene thoresen','georg brandes']}


def normalize(name):
    name=unicodedata.normalize('NFC',name).casefold()
    name=' '.join(re.sub(r'[^\w\s]',' ',name).replace('_',' ').split())
    return ALIASES.get(name,name)


def identities(doc):
    origin=doc['origin'];filename=origin['archive_member'].rsplit('/',1)[-1]
    if filename in QUARANTINE:return []
    if filename.startswith('NB_Mellomalderballadar_'):return ['collection:norske-mellomalderballadar']
    if filename in FALLBACK:return FALLBACK[filename]
    names=origin['authors']
    if not names:
        title=origin['titles'][0]
        if ':' not in title:raise ValueError('Unreviewed author metadata: '+filename)
        names=[title.split(':',1)[0]]
    result=set()
    for name in names:
        for part in re.split(r'\s+(?:og|&)\s+',name):
            if normalize(part)=='familien lie':result.update(['jonas lie','erik lie'])
            else:result.add(normalize(part))
    if not result or '' in result:raise ValueError('Empty author identity')
    return sorted(result)


def resolve(documents):
    """Union coauthors so a shared author never bridges two evaluated splits."""
    parent={};by_doc={};excluded=[]
    def find(x):
        parent.setdefault(x,x)
        if parent[x]!=x:parent[x]=find(parent[x])
        return parent[x]
    for doc in documents:
        ids=identities(doc)
        if not ids:
            excluded.append({'id':doc['id'],'reason':'Multi-author anthology requires bibliographic resolution before holdout assignment'});continue
        for identity in ids:parent[find(identity)]=find(ids[0])
        by_doc[doc['id']]=ids
    components={}
    for identity in parent:components.setdefault(find(identity),[]).append(identity)
    groups={doc_id:'nb:family:'+sha256('|'.join(sorted(components[find(ids[0])])).encode()) for doc_id,ids in by_doc.items()}
    return groups,{'schema':'hypertrix.nb-group-policy/1','aliases':ALIASES,'fallbacks':FALLBACK,
        'identities':by_doc,'groups':groups,'components':[sorted(c) for c in components.values()],
        'exclusions':excluded,'scope':'Reviewed aliases, coauthors, editions and explicit anonymous collection/work families. Near duplicates remain separately limited.'}
