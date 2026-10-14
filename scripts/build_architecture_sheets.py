"""Vector architecture sheets: implemented roles/flows and candidate hardening.

This is documentation only. No deployments, policies or live runtime are changed.
"""
from pathlib import Path
from xml.sax.saxutils import escape
import math
import matplotlib
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, Table, TableStyle

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/pdf/Hypertrix_Architecture_2026-10-14.pdf'
W,H=landscape(A3)
INK=colors.HexColor('#18343F');TEAL=colors.HexColor('#087F83');BLUE=colors.HexColor('#346DB1')
MUTED=colors.HexColor('#657881');LINE=colors.HexColor('#B9CFD3');LIGHT=colors.HexColor('#F0F7F8')
AMBER=colors.HexColor('#A9650C');PALE=colors.HexColor('#FFF5E5')


def fonts():
    folder=Path(matplotlib.get_data_path())/'fonts/ttf'
    for name,file in [('Diagram','DejaVuSans.ttf'),('DiagramBold','DejaVuSans-Bold.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(folder/file)))
    pdfmetrics.registerFontFamily('Diagram',normal='Diagram',bold='DiagramBold',italic='Diagram',boldItalic='DiagramBold')


def para(c,text,x,top,width,size=9.2,leading=None,color=INK,bold=False):
    style=ParagraphStyle('diagram',fontName='DiagramBold' if bold else 'Diagram',fontSize=size,
                         leading=leading or size*1.32,textColor=color,spaceAfter=0)
    p=Paragraph(text,style);_,height=p.wrap(width,1000)
    p.drawOn(c,x,H-top-height)
    return height


def box(c,x,top,width,height,title,body,proposed=False,size=9.2):
    c.saveState();c.setFillColor(PALE if proposed else LIGHT);c.setStrokeColor(AMBER if proposed else LINE)
    c.setLineWidth(.9)
    if proposed:c.setDash(4,3)
    c.roundRect(x,H-top-height,width,height,7,fill=1,stroke=1);c.restoreState()
    th=para(c,title,x+11,top+9,width-22,10.2,color=AMBER if proposed else TEAL,bold=True)
    bh=para(c,body,x+11,top+14+th,width-22,size)
    if th+bh+26>height:raise ValueError('Diagram box overflows: '+title)


def arrow(c,points,color=TEAL,both=False,dashed=False):
    c.saveState();c.setStrokeColor(color);c.setFillColor(color);c.setLineWidth(1.4)
    if dashed:c.setDash(4,3)
    path=c.beginPath();path.moveTo(points[0][0],H-points[0][1])
    for x,y in points[1:]:path.lineTo(x,H-y)
    c.drawPath(path);c.setDash()
    def tip(p,q):
        dx,dy=q[0]-p[0],q[1]-p[1];length=math.hypot(dx,dy);ux,uy=dx/length,dy/length
        a=(q[0]-6*ux+2.6*uy,q[1]-6*uy-2.6*ux);b=(q[0]-6*ux-2.6*uy,q[1]-6*uy+2.6*ux)
        triangle=c.beginPath();triangle.moveTo(q[0],H-q[1]);triangle.lineTo(a[0],H-a[1]);triangle.lineTo(b[0],H-b[1]);triangle.close()
        c.drawPath(triangle,fill=1,stroke=0)
    tip(points[-2],points[-1])
    if both:tip(points[1],points[0])
    c.restoreState()


def label(c,text,x,top,width=150,color=INK):
    style=ParagraphStyle('edge',fontName='DiagramBold',fontSize=8.1,leading=10.1,textColor=color)
    p=Paragraph(text,style);_,height=p.wrap(width,100)
    c.setFillColor(colors.white);c.rect(x-3,H-top-height-2,width+6,height+4,fill=1,stroke=0)
    p.drawOn(c,x,H-top-height)


def header(c,title,subtitle):
    para(c,'HYPERTRIX / ARCHITECTURE ATLAS',36,22,W-72,9.2,color=TEAL,bold=True)
    para(c,title,36,42,W-72,24,bold=True)
    para(c,subtitle,36,79,W-72,10,color=MUTED)


def footer(c,sheet,page_start):
    c.setStrokeColor(LINE);c.line(36,31,W-36,31)
    c.setFillColor(MUTED);c.setFont('Diagram',8)
    c.drawString(36,18,'14 October 2026 | Snapshot of implemented architecture; amber/dashed elements are proposals, not tested controls.')
    c.drawRightString(W-36,18,str(page_start+sheet-1).zfill(2) if page_start else f'Architecture {sheet} / 2')


HARDENING=[
 ('H1 / Role admission and identity',
  'P0/P4: scoped, expiring role credentials; revocation. CA-issued or network-approved certificates are alternatives. A key is not a unique operator.'),
 ('H2 / Eclipse, Sybil and discovery DoS',
  'P6: diverse independent seeds and peer selection; per-peer request/storage quotas. Signed records alone do not make routing Sybil-resistant.'),
 ('H3 / Data availability under churn',
  'P3: replicas on independent hosts; repair and unpredictable retrieval checks. Hashes detect bad bytes; they do not make a missing provider available.'),
 ('H4 / Collusion and dummy approvals',
  'P4: post-commit unpredictable challenges, independent reassignment, canaries and dispute review. Two signatures do not prove honest computation.'),
 ('H5 / Consensus and upgrade authority',
  'P8/P0: independent validator operators, protected remote signers with fencing, threshold-reviewed releases and recovery drills. No automatic slashing now.'),
 ('H6 / Transport, host and telemetry',
  'P9/P7: peer-bound TLS/Noise or managed mTLS; workload ACLs and isolation; quotas and signed telemetry where needed. Transport auth does not grant a role.'),
 ('H7 / Poisoning and source governance',
  'P1/P2: rights/consent policy, deduplication, quarantine and corpus-quality review. Provenance binds origin and bytes, not truth or suitability.'),
 ('H8 / Future training and weights',
  'Future: bind dataset, parent weights, recipe and environment; validate checkpoints and evaluations before promotion. Exact Gather is not training reproducibility.'),
]


def role_sheet(c,page_start):
    header(c,'Node roles, artifact flow and ledger decisions',
           'Logical responsibilities are shown separately; the newcomer combines ingestion/tokenization and one of the three Cortex providers in one container.')
    # Explicit legend: arrowheads are payload/command direction; two ends mean request/response.
    arrow(c,[(37,111),(71,111)]);para(c,'artifact / local publication',80,104,190,8.5)
    arrow(c,[(282,111),(316,111)],BLUE);para(c,'signed command / certified state',325,104,250,8.5)
    arrow(c,[(582,111),(616,111)],MUTED);para(c,'observation (not consensus)',625,104,200,8.5)
    para(c,'P0-P9: protocol inventory on the next sheet.',860,104,292,8.6,color=MUTED)

    box(c,36,141,208,80,'SOURCE CORPUS','Project Gutenberg edition bytes<br/>Source URLs + corpus-lock hashes<br/>P1 / H7')
    box(c,288,141,234,103,'CONTRIBUTOR / TOKENIZER','Own key; pinned tokenizer and E<br/>Partition, tokenize, build vector hashes<br/>Sign source/token/vector lineage<br/>P1 + P2 + P4 / H1, H7')
    box(c,570,141,260,103,'PUBLIC BOOTSTRAP / OPERATOR','P0: network, genesis, plan, trust and image pins<br/>Public bundle only to joiners; own keys<br/>Applied before joining every node role<br/>Current upgrade authority is coordinated / H1, H5')

    box(c,36,303,224,128,'CORTEX UNITS x3 / PROVIDER','2 baseline + 1 newcomer provider<br/>Content-addressed source and tokens<br/>Frozen Source Code Matrix + indexes<br/>Signed lineage and contribution manifest<br/>Private HTTP :8900 / P3 / H3, H7')
    box(c,306,303,238,128,'CPU DAEMONS x20 / AUDITOR','Claim/renew bounded Execution Leases<br/>Fetch, retokenize and replay on NumPy<br/>Compare committed vector SHA-256<br/>Submit signed pass/invalid/unavailable<br/>2 distinct admitted keys/job / H4')
    box(c,592,303,238,177,'THE ORACLE x4','VALIDATOR + LEDGER_REPLICA hats<br/>P8: CometBFT P2P :26656; 3/4 quorum<br/>Private Unix-socket ABCI per validator<br/>Replicated deterministic application state<br/>P4/P5: private HTTP RPC :26657<br/><br/>Orders provider joins, leases, claims and<br/>Blueprints. Does not fetch or replay data.<br/>Evidence recorded; no penalties / H5',size=9.0)

    box(c,36,512,224,112,'GPU DAEMON x1 / DIAGNOSTIC','Own key; full certified-Blueprint replay<br/>CUDA Gather == NumPy == vector hash<br/>Signed local report; status :8883<br/>Advertises AUDITOR; not admitted<br/>No lease, consensus receipt or training / H4')
    box(c,306,512,238,112,'BLUEPRINT PUBLISHER x1','Reads certified job coverage (P5)<br/>P4: publish parent-linked dataset release<br/>P4: coordinated protocol activation<br/>LEDGER_REPLICA discovery hat<br/>Not an additional consensus vote / H5')
    box(c,592,540,238,107,'READERS + HYPERTRIX CONSOLES','P5: verify genesis, header, state and age<br/>P7: aggregate node observations<br/>Keyless input inspector reads Frags<br/>Main Consoles :8882 / HTTPS :443<br/>GPU report has separate UI / H6')

    arrow(c,[(244,180),(288,180)]);label(c,'P1',253,161,22,TEAL)
    arrow(c,[(405,244),(405,267),(148,267),(148,303)]);label(c,'P2: local signed publication',166,254,197,TEAL)
    arrow(c,[(522,214),(545,214),(545,280),(710,280),(710,303)],BLUE)
    label(c,'P4: announce + submit manifest',574,266,212,BLUE)
    arrow(c,[(260,350),(306,350)],both=True);label(c,'P3a',265,331,31,TEAL)
    arrow(c,[(544,350),(592,350)],BLUE,both=True);label(c,'P4/P5',545,332,43,BLUE)
    arrow(c,[(148,431),(148,512)],both=True);label(c,'P3b',160,465,38,TEAL)
    arrow(c,[(592,455),(281,455),(281,565),(260,565)],BLUE,both=True)
    label(c,'P5: certified Blueprint',330,441,171,BLUE)
    arrow(c,[(426,512),(426,493),(710,493),(710,480)],BLUE,both=True)
    label(c,'P4/P5',487,482,53,BLUE)
    arrow(c,[(711,480),(711,540)],BLUE,both=True);label(c,'P5',723,510,25,BLUE)
    arrow(c,[(148,624),(148,657),(575,657),(575,599),(592,599)],MUTED)
    label(c,'P7: diagnostic observations / site feed',189,645,307,MUTED)

    box(c,36,679,794,64,'THE COLLECTIVE / P6 DISCOVERY ON EVERY NODE',
        'Distributed Kademlia-style HTTP :8903. Signed role/endpoint records, XOR routing, expiry and sequence checks. Lookup does not authorize a role. H1/H2.',size=8.8)
    box(c,36,750,794,53,'FUTURE PATH / NOT EXECUTED IN THIS PROTOTYPE',
        'Certified Blueprint + parent weights  ->  training node  ->  checkpoint validation / evaluations  ->  weight-ledger promotion. H8.',proposed=True,size=9.1)

    for i,(title,body) in enumerate(HARDENING):
        box(c,856,141+i*82,298,76,title,body,proposed=True,size=8.6)
    footer(c,1,page_start)


PROTOCOLS=[
 ('P0','Bootstrap / runtime trust','Public files -> every role; operator controls issuance','SHA-256 network/genesis/plan/image pins; Ed25519 registrations. Own private state per node. Runtime upgrade is publisher-signed, not threshold governance.'),
 ('P1','Source acquisition','External corpus -> contributor; HTTPS','Original edition bytes + URL/lock. Content hashing records what was fetched; HTTPS and lineage do not establish factual quality or rights.'),
 ('P2','Ingestion / local provenance','Contributor -> its Cortex object store; local calls/files','Pinned tokenizer; uint32-le tokens; E[t] float32-le; SHA-256 artifact descriptors; Ed25519 signed parent DAG and author sequence. Dense vectors discarded.'),
 ('P3a / P3b','Artifact pull / data plane','Private HTTP :8900 -> requester caches','CPU Puller: 1-MiB pieces, deduplication, bounded concurrency, retry/failover, final whole-object hash. GPU/public reader: bounded whole objects. Frozen E is transferred once per empty cache.'),
 ('P4','Signed commands / leases','Contributor, admitted CPU Daemons and publisher -> Oracle RPC :26657','Canonical JSON + Ed25519; chain/plan binding, actor sequence and validity height. Announce/submit, claim/renew/result and publish. GPU diagnostic does not send these transactions.'),
 ('P5','Certified reads','Oracle RPC :26657 -> CPU/GPU nodes, publisher, readers and Consoles','Go/CometBFT verifies the fixed-genesis quorum header and state app hash. Minimum accepted height + age window guard reads. Blueprint manifests then bind artifact fetches.'),
 ('P6','Role discovery / peer routing','Each peer <-> peers/seeds; HTTP :8903','160-bit XOR distance; k=8, alpha=3; signed endpoint/role records, sequence and expiry, lifetime <=180 s. Announcements refresh every 25 s. Invited loopback routes, not open Internet discovery.'),
 ('P7','Observability / public inspection','Node JSON files/site HTTP -> Console; browser HTTPS :443','Private cloud feed :8901, local feed forwarded :8902; local Console :8882 and GPU page :8883. Certificates are checked separately from unsigned telemetry. Read-only UI; no runtime socket.'),
 ('P8','Consensus / application execution','Oracle <-> Oracle on TCP :26656; private ABCI Unix sockets','CometBFT fixed voting set; 3 of 4 votes. Per-validator deterministic state and persistent signer state. Incident/conflicting-result evidence can be recorded; no automatic financial punishment.'),
 ('P9','Cross-host pilot transport','Local outbound SSH -> Azure gateway TCP :2222','Pinned SSH host key and operator authentication; allowlisted forwards. Separate control and bulk-data connections plus GPU DHT route. HTTP is private inside this mesh; no mesh-wide peer mTLS yet.'),
]


def protocol_sheet(c,page_start):
    header(c,'Transport boundaries and protocol inventory',
           'P0-P9 identify the actual paths on the role diagram. All amber controls remain candidates; they have not been enabled by this documentation change.')
    box(c,36,115,352,135,'LOCAL HOST / ONE FAILURE DOMAIN',
        '2 Oracle + 2 Cortex + 16 CPU Daemons<br/>Publisher + GPU diagnostic + local Console<br/>23 containers total; own node keys/state mounts<br/>ABCI uses each validator\'s private Unix socket<br/>Public bootstrap is read-only for new joiners.',size=9.8)
    box(c,694,115,460,135,'AZURE ACI / ONE FAILURE DOMAIN',
        '2 Oracle + 1 Cortex + 4 CPU Daemons + gateway + Console<br/>9 containers; shared host, networking and lifecycle<br/>Gateway has no validator keys; restricted forwarding targets<br/>Public :2222 -> SSH gateway; :80/:443 -> Caddy -> Console :8882<br/>Private application/DHT ports are not public ingress.',size=9.8)
    arrow(c,[(388,173),(694,173)],BLUE,both=True)
    label(c,'P9 / AUTHENTICATED SSH',424,150,236,BLUE)
    para(c,'Control: P4/P5/P6/P8 + telemetry<br/>Bulk artifacts: P3<br/>Additional route: GPU P6 only',431,188,243,9.4,color=MUTED)
    para(c,'CURRENT TRUST BOUNDARY: one operator controls both sites, bootstrap and upgrade activation. Containers separate state; they do not create independent operators.',36,264,W-72,10.0,bold=True)
    headers=['ID','Interface','Sender / receiver / transport','Payload, checks and important limits']
    style=ParagraphStyle('table',fontName='Diagram',fontSize=8.9,leading=11.7,textColor=INK)
    hs=ParagraphStyle('thead',fontName='DiagramBold',fontSize=9,leading=12,textColor=colors.white)
    data=[[Paragraph(escape(t),hs) for t in headers]]
    data += [[Paragraph(escape(t),style) for t in row] for row in PROTOCOLS]
    table=Table(data,colWidths=[61,155,284,W-72-500],repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),TEAL),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[LIGHT,colors.white]),('LINEBELOW',(0,-1),(-1,-1),.5,LINE)]))
    _,height=table.wrap(W-72,H)
    top=302
    if top+height>765:raise ValueError(f'Protocol table overflows: {height}')
    table.drawOn(c,36,H-top-height)
    para(c,'ROLE SCOPE: VALIDATOR, PROVIDER, TOKENIZER, AUDITOR and LEDGER_REPLICA are advertised hats. Actual audit/vote authority comes from admission rules; the GPU only advertises AUDITOR. TRAINER_GPU is a planned execution capability, not a running training node.',36,top+height+12,W-72,9.2,color=MUTED)
    para(c,'Implementation references: client.py, state.py, incidents.py, worker.py, dht.py, transfer.py, contributor_node.py, gpu_reconstruction_node.py and hybrid_observer.py. Security rationale and validation criteria: docs/architecture_notes.md.',36,top+height+44,W-72,8.3,color=MUTED)
    footer(c,2,page_start)


def build(output=OUT,page_start=None):
    fonts();output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    c=canvas.Canvas(str(output),pagesize=(W,H),pageCompression=1)
    c.setTitle('Hypertrix architecture: node roles, protocols, flows and proposed hardening')
    c.setAuthor('Espen');role_sheet(c,page_start);c.showPage();protocol_sheet(c,page_start);c.save()
    return output


if __name__=='__main__':print(build())
