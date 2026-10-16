"""Typeset the verified Seed architecture as a vector PDF, without touching training."""
from pathlib import Path
import math
import json
from xml.sax.saxutils import escape

from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output/pdf/Hypertrix_Seed_Model_Architecture.pdf'
W, H = 595.276, 841.89
M, RIGHT = 42, W-42
INK, MUTED, LINE = '#17283D', '#526478', '#D7DEE7'
COLORS = {
    'data': ('#2463C5', '#16499B'), 'attention': ('#7540BC', '#572A94'),
    'ff': ('#00806D', '#006151'), 'training': ('#F6BB29', '#AA7400'),
    'state': ('#C4D1E0', '#63768E'),
}
for name, filename in [('Body','arial.ttf'),('Bold','arialbd.ttf'),('Italic','ariali.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(Path('C:/Windows/Fonts')/filename)))
pdfmetrics.registerFontFamily('Body', normal='Body', bold='Bold', italic='Italic', boldItalic='Bold')
OUT.parent.mkdir(parents=True, exist_ok=True)
c = canvas.Canvas(str(OUT), pagesize=(W,H), pageCompression=1)
c.setTitle('Hypertrix Seed - Model Architecture')
c.setAuthor('Hypertrix project')
c.setSubject('Seed I, Seed I MoE, Seed II and Seed III architectures and training lineage')


def text(x, y, value, size=10, font='Body', color=INK):
    c.setFont(font, size); c.setFillColor(HexColor(color)); c.drawString(x,H-y,value)


def para(x,y,width,value,size=10,leading=14,color=INK,align=0):
    p=Paragraph(value, ParagraphStyle('p',fontName='Body',fontSize=size,leading=leading,
                                     textColor=HexColor(color),alignment=align))
    _,height=p.wrap(width,1000)
    if y+height>H-42: raise ValueError('Text exceeds page: '+value[:60])
    p.drawOn(c,x,H-y-height)
    return y+height


def box(x,y,w,h,title,detail='',kind='state',size=10):
    fill,stroke=COLORS[kind]
    c.setFillColor(HexColor(fill));c.setStrokeColor(HexColor(stroke));c.setLineWidth(.8)
    c.roundRect(x,H-y-h,w,h,7,stroke=1,fill=1)
    content='<b>'+escape(title)+'</b>'
    if detail: content+='<br/><font size="'+str(size-1)+'">'+detail+'</font>'
    p=Paragraph(content,ParagraphStyle('node',fontName='Body',fontSize=size,leading=size+3,
                                      textColor=HexColor('#FFFFFF' if kind in ('data','attention','ff') else INK),alignment=1))
    _,ph=p.wrap(w-16,h)
    if ph>h-8:raise ValueError('Node overflow: '+title)
    p.drawOn(c,x+8,H-y-(h+ph)/2)


def arrow(points,dashed=False,color='#748092'):
    c.setStrokeColor(HexColor(color));c.setFillColor(HexColor(color));c.setLineWidth(.9)
    c.setDash(3,3) if dashed else c.setDash()
    path=c.beginPath();path.moveTo(points[0][0],H-points[0][1])
    for x,y in points[1:]:path.lineTo(x,H-y)
    c.drawPath(path)
    c.setDash()
    (x0,y0),(x,y)=points[-2:];angle=math.atan2(y-y0,x-x0)
    p=c.beginPath();p.moveTo(x,H-y)
    for offset in (-.45,.45):p.lineTo(x-5*math.cos(angle+offset),H-(y-5*math.sin(angle+offset)))
    p.close();c.drawPath(p,stroke=0,fill=1)


def add(x,y):
    c.setStrokeColor(HexColor(COLORS['state'][1]));c.setFillColor(HexColor(COLORS['state'][0]))
    c.circle(x,H-y,9,fill=1,stroke=1)
    text(x-3.2,y+3.4,'+',12)


def page(number,kicker,title,subtitle):
    c.setFillColor(HexColor('#FFFFFF'));c.rect(0,0,W,H,fill=1,stroke=0)
    text(M,31,'H Y P E R T R I X',9,'Bold',COLORS['attention'][1])
    text(RIGHT-82,31,'ARCHITECTURE',8,'Bold',MUTED)
    c.setStrokeColor(HexColor(LINE));c.setLineWidth(.6);c.line(M,H-43,RIGHT,H-43)
    text(M,65,kicker.upper(),8,'Bold',MUTED)
    text(M,91,title,23,'Bold')
    para(M,105,RIGHT-M,subtitle,10,14,MUTED)
    c.setStrokeColor(HexColor(LINE));c.line(M,35,RIGHT,35)
    text(M,H-23,'Seed family  /  Architecture snapshot: 16 October 2026',8,color=MUTED)
    text(RIGHT-32,H-23,str(number),8,color=MUTED)


def section(y,title):text(M,y,title,13,'Bold')


def legend(y):
    x=M
    for kind,label,width in [('data','Data / embedding',114),('attention','Attention',87),('ff','Feed-forward',102),('training','Training',77),('state','State / records',100)]:
        c.setFillColor(HexColor(COLORS[kind][0]));c.setStrokeColor(HexColor(COLORS[kind][1]))
        c.roundRect(x,H-y-9,9,9,2,fill=1,stroke=1)
        text(x+14,y+8,label,8,color=MUTED);x+=width


# Page 1: family tree. Dashed links express design evolution, not weight lineage.
page(1,'01 / Family','Our transformer models',
     'Three dense generations, one expert branch. All dense generations began with random weights.')
legend(145)
box(47,185,220,74,'Seed I - dense','5,773,824 parameters<br/>6 layers · width 256 · 4 heads', 'data',11)
box(330,185,218,74,'Seed I - MoE','10,495,488 parameters<br/>4 experts; select 2 per token','ff',11)
arrow([(267,222),(330,222)])
para(274,181,50,'Copy<br/>learned<br/>FFNs',8,11,MUTED,1)
box(47,326,220,74,'Seed II - dense','40,645,120 parameters<br/>12 layers · width 512 · 8 heads','data',11)
arrow([(157,259),(157,326)],True)
para(177,277,137,'Fresh weights<br/>RMSNorm + SwiGLU',9,12,MUTED)
box(47,467,220,74,'Seed III - dense','125,857,536 parameters<br/>18 layers · width 768 · 12 Q / 4 KV','data',11)
arrow([(157,400),(157,467)],True)
para(177,418,143,'Fresh weights<br/>GQA + larger vocabulary',9,12,MUTED)
para(332,290,208,'<b>Solid arrow: inherited weights</b><br/>The MoE branch copies our trained Seed I feed-forward layers into expert banks and continues training.',10,15)
para(332,379,208,'<b>Dashed arrows: design evolution</b><br/>Seed II and Seed III use new random initialization. They do not inherit the preceding generation\'s weights.',10,15)
para(332,483,208,'<b>Snapshot status</b><br/>Seed I, its MoE branch and Seed II completed training. A host crash interrupted Seed III at update 5,590 of 6,000; its signed checkpoint at 5,500 survived.',10,15)
section(600,'The shared design')
para(M,614,RIGHT-M,'Each model is a causal, decoder-only transformer: it predicts the next token from preceding tokens. Input embeddings and the output projection share one learned matrix. All generations use rotary positional encoding (RoPE) and residual connections.',11,16)
para(M,691,RIGHT-M,'The learned token embeddings are separate from the fixed <b>Source Code Matrix</b> used in earlier vector reconstruction experiments. Transformer blocks and experts are modules inside the model, not network nodes.',10,15)
c.showPage()

# Page 2: configuration table, readable and independently useful.
page(2,'02 / Specifications','What changed between generations',
     'Values come from the saved run configurations and model implementations.')
rows=[
 ('Parameters','5,773,824','10,495,488','40,645,120','125,857,536'),
 ('Active / token','All','~7,349,760','All','All'),
 ('Decoder blocks','6','6','12','18'),('Residual width','256','256','512','768'),
 ('Query / KV heads','4 / 4','4 / 4','8 / 8','12 / 4'),('Head dimension','64','64','64','64'),
 ('FFN width','1,024','1,024 / expert','1,408','2,048'),('FFN activation','GELU','GELU','SwiGLU','SwiGLU'),
 ('Normalization','LayerNorm','LayerNorm','RMSNorm','RMSNorm'),('Context tokens','512','512','512','1,024'),
 ('BPE vocabulary','4,096','4,096','4,096','16,384'),('Position encoding','RoPE','RoPE','RoPE','RoPE'),
 ('Embedding / head','Tied','Tied','Tied','Tied')]
xs=[M,182,274,366,458]; widths=[140,92,92,92,95]
for i,label in enumerate(['Configuration','Seed I','Seed I MoE','Seed II','Seed III']):
    c.setFillColor(HexColor('#17283D'));c.rect(xs[i],H-165-32,widths[i],32,fill=1,stroke=0)
    para(xs[i]+6,174,widths[i]-12,'<b>'+label+'</b>',8.5,11,'#FFFFFF')
y=197
for ri,row in enumerate(rows):
    if ri%2==0:
        c.setFillColor(HexColor('#F5F7FA'));c.rect(M,H-y-28,RIGHT-M,28,fill=1,stroke=0)
    for i,value in enumerate(row):para(xs[i]+6,y+8,widths[i]-12,escape(value),8.2,10)
    y+=28
section(601,'Capacity, context and computation')
para(M,615,RIGHT-M,'<b>Depth</b> is the number of successive transformer blocks. <b>Width</b> is the size of each token\'s evolving representation. <b>Context</b> limits how many tokens one forward pass can attend to. <b>Vocabulary</b> sets the number of possible next-token outputs.',10,15)
para(M,682,RIGHT-M,'The MoE parameter count includes every expert, while a token activates two experts in each expert layer. Seed III is dense: its SwiGLU gate acts on features and does not select experts. Every generation uses generative next-token pretraining.',10,15)
c.showPage()

# Page 3: Seed III residual block, with explicit fork to learning/inference.
page(3,'03 / Seed III','From tokens to a continuation',
     'B = batch size; T = sequence length, up to 1,024. The residual stream has width 768.')
legend(143)
x,w=155,285
box(x,174,w,38,'Text / code → byte-level BPE','16,384-token vocabulary','data')
arrow([(297,212),(297,228)])
box(x,228,w,40,'Token IDs → learned embedding E','E: 16,384 × 768; hidden states: B × T × 768','data')
arrow([(297,268),(297,301)])
# Group outline is structural; node colors encode operation type.
c.setStrokeColor(HexColor(LINE));c.setFillColor(HexColor('#FFFFFF'))
c.roundRect(119,H-291-274,354,274,9,fill=0,stroke=1)
text(130,285,'REPEAT BLOCK 18 TIMES - DISTINCT WEIGHTS',8,'Bold',MUTED)
box(x,301,w,29,'RMSNorm','', 'state')
arrow([(297,330),(297,341)])
box(x,341,w,42,'Causal grouped-query attention','12 Q heads / 4 K,V heads; RoPE on Q and K','attention')
arrow([(297,383),(297,398)]);add(297,407)
arrow([(297,292),(133,292),(133,407),(288,407)])
text(48,352,'Residual',8,color=MUTED)
arrow([(297,416),(297,430)])
box(x,430,w,29,'RMSNorm','', 'state')
arrow([(297,459),(297,471)])
box(x,471,w,42,'SwiGLU feed-forward','768 → 2,048 → 768','ff')
arrow([(297,513),(297,535)]);add(297,544)
arrow([(297,421),(458,421),(458,544),(306,544)])
text(483,485,'Residual',8,color=MUTED)
arrow([(297,553),(297,578)])
box(x,578,w,29,'Final RMSNorm','', 'state')
arrow([(297,607),(297,619)])
box(x,619,w,43,'Tied output projection: E transpose','Logits: B × T × 16,384','data')
arrow([(155,248),(105,248),(105,640),(155,640)],True,COLORS['data'][1])
para(43,583,57,'Shared<br/>weights E',8,11,COLORS['data'][1])
arrow([(297,662),(297,681),(164,681),(164,696)])
arrow([(297,681),(431,681),(431,696)])
box(44,696,239,56,'Generation','Next-token probabilities → choose token<br/>Append to the prefix and repeat','data')
box(311,696,239,56,'Training','Next-token cross entropy → backward<br/>AdamW updates every model parameter','training')
para(M,766,RIGHT-M,'Padding targets are excluded from the loss. Generation uses the final position; training predicts at every valid position.',8.5,12,MUTED)
c.showPage()

# Page 4: attention and gated dense feed-forward.
page(4,'04 / Inside the block','Attention and feature transformation',
     'Attention mixes information between tokens; SwiGLU transforms each token\'s features.')
box(167,157,260,35,'Normalized hidden state','B × T × 768','state')
for bx,title,detail in [(44,'Query projection','12 heads × 64'),(224,'Key projection','4 heads × 64'),(404,'Value projection','4 heads × 64')]:
    arrow([(297,192),(297,207),(bx+73,207),(bx+73,222)])
    box(bx,222,147,43,title,detail,'attention')
box(44,283,147,29,'RoPE on queries',kind='attention')
box(224,283,147,29,'RoPE on keys',kind='attention')
arrow([(117,265),(117,283)]);arrow([(297,265),(297,283)])
arrow([(117,312),(117,329),(207,329),(207,344)])
arrow([(297,312),(297,344)])
box(105,344,274,48,'Attention scores','Q K transpose / √64<br/>Causal mask → softmax','attention')
arrow([(242,392),(242,420)])
arrow([(477,265),(477,444),(379,444)])
box(105,420,274,48,'Weighted sum of values','3 query heads share each KV head','attention')
arrow([(242,468),(242,486)])
box(105,486,274,41,'Concatenate 12 heads → output','768 → 768','attention')
para(401,480,147,'KV cache during generation:<br/><b>1/3 the KV elements</b> of a 12-KV-head design at the same context and precision.',8.5,12,MUTED)
para(M,545,RIGHT-M,'RoPE rotates pairs of query/key coordinates by token position; it does not add learned position embeddings. The causal mask blocks future tokens. Keys and values are cached during generation.',9.5,14)
section(609,'SwiGLU: a dense feature gate')
box(45,632,100,39,'Input x','Width 768','state')
box(181,621,145,30,'SiLU(gate(x))',kind='ff')
box(181,669,145,30,'up(x)',kind='ff')
arrow([(145,651),(162,651),(162,636),(181,636)])
arrow([(162,651),(162,684),(181,684)])
arrow([(326,636),(348,636),(348,660),(365,660)])
arrow([(326,684),(348,684),(348,660)])
box(365,640,184,40,'Elementwise product → down','2,048 → 768','ff')
para(M,718,RIGHT-M,'<b>FFN(x) = down[SiLU(gate(x)) × up(x)]</b> (elementwise product)<br/>Both gate and up map 768 to 2,048 dimensions. All linear projections are bias-free; the model uses no dropout. RMSNorm epsilon is 1e-6.',10,15)
c.showPage()

# Page 5: experts and mathematical objective.
page(5,'05 / Expert branch','How Seed I uses a mixture of experts',
     'Expert banks replace the feed-forward transform in blocks 2, 4 and 6. Other blocks stay dense.')
legend(143)
box(169,182,255,40,'Normalized token state','Width 256','state')
arrow([(297,222),(297,246)])
box(169,246,255,48,'Learned router','Softmax over 4 experts → choose top 2<br/>Renormalize the 2 selected weights','ff')
for i,bx in enumerate([44,177,310,443]):
    arrow([(297,294),(297,315),(bx+54,315),(bx+54,336)])
    box(bx,336,108,63,f'Expert {i+1}','GELU FFN<br/>256 → 1,024<br/>→ 256','ff',9)
    arrow([(bx+54,399),(bx+54,422),(297,422),(297,443)])
arrow([(169,202),(30,202),(30,325),(98,325),(98,336)],True)
for bx in [177,310,443]:
    arrow([(30,325),(bx+54,325),(bx+54,336)],True)
para(45,274,107,'Original token state supplied to selected experts',8,11,MUTED)
box(169,443,255,45,'Weighted sum of selected outputs','Only 2 expert paths execute per token','ff')
arrow([(297,488),(297,507)])
box(169,507,255,35,'Add residual → width 256',kind='state')
para(M,562,RIGHT-M,'At conversion, every expert copies the corresponding trained dense feed-forward weights. The router is newly initialized, and AdamW state is reset. Continued training can differentiate experts. A stable expert-ID tie-break resolves equal router scores.',10,15)
section(644,'Optimization objective')
para(M,658,RIGHT-M,'<b>L<sub>next-token</sub> = -(1/N) Σ<sub>i in nonpadding targets</sub> log p<sub>θ</sub>(x<sub>i</sub> | x<sub>&lt;i</sub>)</b><br/><br/>For the MoE branch: <b>L = L<sub>next-token</sub> + 0.01 × mean router balancing loss</b>. The auxiliary term discourages unused experts. Validation loss selects the checkpoint.',10,16)
para(M,744,RIGHT-M,'These experts execute within one model process. Expert routing does not distribute them across network machines in this experiment.',9,13,MUTED)
c.showPage()

# Page 6: network system and auditable references.
page(6,'06 / Execution and evidence','The model inside the network',
     'Data lineage, model training, replay verification and inference are separate operations.')
box(44,156,145,60,'Source documents','Origin links<br/>Content hashes','data')
box(224,156,145,60,'Blueprint','Tokenizer + splits<br/>Committed token arrays','data')
box(404,156,147,60,'Trainer','Initialization + recipe<br/>Forward / loss / backward','training')
arrow([(189,186),(224,186)]);arrow([(369,186),(404,186)])
arrow([(477,216),(477,258)])
box(404,258,147,56,'Checkpoint','Weights + optimizer<br/>Parent and artifact digests','state')
arrow([(404,286),(369,286)])
box(224,258,145,56,'Replay verifier','Repeat recorded updates<br/>Compare tensors','training')
arrow([(477,314),(477,360),(369,360)])
box(224,335,145,50,'Consumer / studio','Load selected checkpoint<br/>Generate continuation','data')
box(44,258,145,127,'Signed lineage','Blueprint, recipe,<br/>implementation,<br/>parent checkpoint,<br/>results and receipts','state')
arrow([(296,216),(296,236),(116,236),(116,258)],True)
arrow([(224,286),(189,286)],True)
arrow([(477,314),(477,405),(116,405),(116,385)],True)
section(443,'What the architecture diagram represents')
para(M,457,RIGHT-M,'The current main run uses one GPU for full-model backpropagation. Distributed DDP and Oracle adapter coordination have separate test records. Transformer depth does not imply deployment across that many nodes. Investigator source assessment runs after generation, outside the transformer.',10,15)
para(M,528,RIGHT-M,'Seed III uses AdamW, BF16 execution with FP32 master weights and optimizer state, and tied input/output weights. Residual output projections start at standard deviation 0.02 / √(2 × 18). Separate-process replay checks the recorded update interval against its checkpoint and optimizer state.',10,15)
section(614,'Implementation and run records')
references=[
 ('Seed I and MoE','src/dawl_scratch/model.py; docs/results/scratch-001/*-config.json'),
 ('Seed II','src/dawl_seed/model.py; docs/results/scratch-003/dense/config.json'),
 ('Seed III','src/dawl_lab/model.py; docs/results/seed-lab-001/run/recipe.json'),
 ('Methodology','docs/scratch_model.md; docs/seed_expansion.md; docs/home_training_lab.md'),
 ('Editable diagram source','docs/seed_model_architecture.md'),
]
y=630
for label,path in references:
    y=para(M,y,RIGHT-M,'<b>'+label+'</b> - '+escape(path),8.5,12)+7
para(M,742,RIGHT-M,'Diagrams summarize code and saved configurations inspected on 16 October 2026. Run-specific replay coverage and results are documented in the corresponding evidence bundles.',8.5,12,MUTED)
c.showPage()

# Remaining pages: unchanged 128-token outputs and all sentence-level judgments.
examples=ROOT/'docs/results/seed-architecture-samples-002'
samples=json.loads((examples/'samples.json').read_bytes())
investigations=json.loads((examples/'investigations.json').read_bytes())
replay=json.loads((examples/'semantic-replay.json').read_bytes())
if not replay['accepted']:raise ValueError('Semantic replay did not match')
verdict_names={'supported':'Support flagged','contradicted':'Contradiction flagged',
               'insufficient_evidence':'Insufficient evidence','conflicting':'Conflicting evidence'}
number=6


def appendix_page(title,subtitle,kicker='Output and evidence'):
    global number
    number+=1
    page(number,f'{number:02d} / {kicker}',title,subtitle)


def height(value,width,size=10,leading=14):
    return Paragraph(value,ParagraphStyle('measure',fontName='Body',fontSize=size,leading=leading)).wrap(width,1000)[1]


def short(value,limit):
    value=' '.join(value.split())
    return value if len(value)<=limit else value[:limit].rsplit(' ',1)[0]+'…'


def source_link(candidate):
    title=escape(short(candidate.get('title') or candidate['document'],100))
    origin=candidate.get('origin') or {}
    url=origin.get('url') or origin.get('download',{}).get('url')
    if url and url.startswith(('https://','http://')):
        title='<link href="'+escape(url,{'"':'&quot;'})+'" color="#16499B">'+title+'</link>'
    return title


for sample,investigation in zip(samples['samples'],investigations['samples']):
    appendix_page(sample['label']+' - continuation',
                   'Seed 42 · temperature 0.7 · 128 new tokens. One unedited output from the selected checkpoint.')
    response=sample['response'];identity=response.get('checkpoint_sha256',response.get('checkpoint'))
    box(M,149,RIGHT-M,47,'Prompt',escape(sample['request']['prompt']),'data',10)
    text(M,218,'CONTINUATION',8,'Bold',MUTED)
    continuation=escape(response['text']).replace('\r\n','\n').replace('\n','<br/>')
    ch=height(continuation,RIGHT-M-26,10,14)+20
    c.setFillColor(HexColor('#F1F5FA'));c.roundRect(M,H-229-ch,RIGHT-M,ch,5,fill=1,stroke=0)
    para(M+13,239,RIGHT-M-26,continuation,10,14)
    y=229+ch+20
    text(M,y,f"Checkpoint {response['step']:,}  ·  {response['tokens']} tokens  ·  SHA-256 {identity[:16]}...",8.5,color=MUTED)
    y=para(M,y+10,RIGHT-M,'Sampler: '+escape(sample['sampler'])+'.',8.5,12,MUTED)+27
    section(y,'Investigator assessment by sentence span')
    y+=14
    for i,claim in enumerate(investigation['claims'],1):
        r=claim['result']
        row=f'<b>{i:02d}. {verdict_names[r["verdict"]]}</b> · '+escape(short(r['text'],100))
        y=para(M,y,RIGHT-M,row,9,13)+8
    y=para(M,y+6,RIGHT-M,'The assessed text includes the prompt. Boundaries preserve exact wording; the trailing unfinished span is retained. The following pages list every retrieved candidate and its individual judgment.',9,13,MUTED)+10
    para(M,y,RIGHT-M,'Reference pool: Seed III training corpus, 145,011 documents and 597,868 indexed passages. The same pool is used for every model; membership alone does not establish earlier models\' exposure or causal influence.',8.5,12,MUTED)
    c.showPage()

    evidence_subtitle='Candidates in retrieval order. E / N / C = raw entailment / neutral / contradiction scores; not calibrated confidence.'
    appendix_page(sample['label']+' - corpus evidence',evidence_subtitle)
    y=151
    for i,claim in enumerate(investigation['claims'],1):
        r=claim['result'];entries=[]
        entries.append((f'<b>Span {i:02d} · {verdict_names[r["verdict"]]}</b><br/>'+escape(short(r['text'],125)),9,12,INK,7))
        for j,(candidate,judgment) in enumerate(zip(claim['candidates'],r['judgments']),1):
            scores=judgment['raw_nli']['scores']
            raw=(f"E/N/C {scores['entailment']:.3f}/{scores['neutral']:.3f}/{scores['contradiction']:.3f}" if scores else 'Context limit')
            verdict='Insufficient' if judgment['verdict']=='insufficient_evidence' else verdict_names[judgment['verdict']]
            metadata=f" · {candidate['source_sha256'][:8]}... · chars {candidate['start']}:{candidate['end']}"
            detail=escape(verdict)+' · '+raw+metadata
            content=f'<b>{j}. {source_link(candidate)}</b><br/><font size="7.6">'+detail+'</font>'
            entries.append((content,8.5,11,INK,7))
            if j==1:
                excerpt='“'+escape(short(candidate['text'],145))+'”'
                entries.append((excerpt,8.5,11,MUTED,3))
        if not claim['candidates']:
            entries.append(('No candidate was retrieved for this span.',9,12,MUTED,7))
        needed=sum(height(v,RIGHT-M,s,l)+gap for v,s,l,co,gap in entries)+10
        if y+needed>752:
            c.showPage()
            appendix_page(sample['label']+' - corpus evidence',evidence_subtitle+' Continued.')
            y=151
        if y+needed>752:raise ValueError('Evidence block exceeds one page')
        c.setStrokeColor(HexColor(LINE));c.line(M,H-y+5,RIGHT,H-y+5)
        for value,size,leading,color,gap in entries:
            y=para(M,y,RIGHT-M,value,size,leading,color)+gap
        y+=10
    para(M,761,RIGHT-M,'All source hashes, exact full passages, character offsets, scores and signed receipts are retained in docs/results/seed-architecture-samples-002/. First-candidate excerpts are shown above; titles link to source origins.',8,11,MUTED)
    c.showPage()

appendix_page('What this sample shows','Longer output provides more material to inspect. Missing assessment categories remain missing.','Assessment readout')
claims=[claim for example in investigations['samples'] for claim in example['claims']]
counts={key:sum(claim['result']['verdict']==key for claim in claims) for key in verdict_names}
pairs=sum(len(claim['candidates']) for claim in claims)
y=159
for label in ('supported','contradicted','insufficient_evidence','conflicting'):
    box(M,y,RIGHT-M,43,verdict_names[label],f"{counts[label]} of {len(claims)} assessed spans",'state',10)
    y+=53
section(401,'The observed result')
y=para(M,416,RIGHT-M,f'All {len(claims)} spans received insufficient evidence. The Investigator evaluated {pairs} retrieved passage pairs; one span had no candidate. The continuations and the 0.90 score / 0.20 margin thresholds were not changed to obtain other labels. No artificial outputs were added.',10,15)
y=para(M,y+15,RIGHT-M,'These are fictional continuations, sometimes incomplete or incoherent, rather than a factual question-answer benchmark. An invented scene may have no supporting passage even when well written. Retrieval coverage and the NLI evaluator also limit the result; this is not a measure of model accuracy.',10,15)
section(y+31,'Evidence that can be checked')
y=para(M,y+45,RIGHT-M,f'All source hashes, exact excerpts, Blueprint membership, answer bindings and signed receipts passed verification. A fresh CPU process with no network or signing keys reproduced all {pairs} NLI outputs and verdicts exactly. This is replay within the same hardware/runtime cohort.',10,15)
y=para(M,y+14,RIGHT-M,'The bundle preserves the prompt, sampler, checkpoint identity, unchanged output, selected spans, origin links, complete passages and semantic scores. Checkpoint identity in the collection is a consumer assertion; this NLI replay does not re-run model generation or training.',9,13,MUTED)
para(M,y+14,RIGHT-M,'Recorded inputs and replay: docs/results/seed-architecture-samples-002/. The earlier 32-token experiment remains intact in seed-architecture-samples-001/.',8.5,12,MUTED)
c.save()
print(f'{OUT} ({number} pages)')
