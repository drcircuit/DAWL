"""Build the shareable hybrid update from editable repository Markdown and evidence."""
from pathlib import Path
import json
import re
from xml.sax.saxutils import escape

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Flowable
from pypdf import PdfReader, PdfWriter
from build_architecture_sheets import build as build_architecture

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/pdf/Hypertrix_Hybrid_Research_Update_2026-10-14.pdf'
OUT.parent.mkdir(parents=True,exist_ok=True)
FONT=Path(matplotlib.get_data_path())/'fonts/ttf'
for name,file in [('Body','DejaVuSans.ttf'),('Bold','DejaVuSans-Bold.ttf'),('Mono','DejaVuSansMono.ttf')]:
    pdfmetrics.registerFont(TTFont(name,str(FONT/file)))
pdfmetrics.registerFontFamily('Body',normal='Body',bold='Bold',italic='Body',boldItalic='Bold')
W,H=A4;M=44;CW=W-2*M
INK=colors.HexColor('#182F3A');TEAL=colors.HexColor('#087F83');MUTED=colors.HexColor('#596D78')
LIGHT=colors.HexColor('#F0F6F7');LINE=colors.HexColor('#C8DADD')
styles={
 'body':ParagraphStyle('body',fontName='Body',fontSize=9.1,leading=13.0,textColor=INK,spaceAfter=8),
 'title':ParagraphStyle('title',fontName='Bold',fontSize=34,leading=40,textColor=INK,spaceAfter=18),
 'h1':ParagraphStyle('h1',fontName='Bold',fontSize=21,leading=27,textColor=INK,spaceAfter=17),
 'h2':ParagraphStyle('h2',fontName='Bold',fontSize=12,leading=17,textColor=TEAL,spaceAfter=12),
 'small':ParagraphStyle('small',fontName='Body',fontSize=7.7,leading=10.3,textColor=MUTED,spaceAfter=7),
 'cell':ParagraphStyle('cell',fontName='Body',fontSize=8.0,leading=11.2,textColor=INK),
 'head':ParagraphStyle('head',fontName='Bold',fontSize=8.0,leading=11.2,textColor=colors.white),
 'code':ParagraphStyle('code',fontName='Mono',fontSize=7.2,leading=11.3,textColor=TEAL,spaceAfter=5,leftIndent=10),
}


def markup(s):
    s=escape(s)
    s=re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',s)
    s=re.sub(r'`(.+?)`',r'<font name="Mono" size="8">\1</font>',s)
    s=re.sub(r'\[([^\]]+)\]\(#evidence-guide\)',r'<link href="#evidence-guide" color="#087F83">\1</link>',s)
    s=re.sub(r'(https://[^\s<]+)',r'<link href="\1" color="#087F83">\1</link>',s)
    return s


class Topology(Flowable):
    def __init__(self): Flowable.__init__(self);self.width=CW;self.height=210
    def draw(self):
        c=self.canv;gap=26;bw=(CW-gap)/2;y=52;bh=152
        boxes=[(0,'AZURE / NORWAY EAST','3 vCPU / 8 GB - shared pool',[
            '2 Oracle validators','1 Cortex + 4 CPU Daemons','Transport gateway','Public HTTPS Console','9 containers / one host domain']),
            (bw+gap,'LOCAL / WSL + PODMAN','Local CPU + RTX 5090 Laptop GPU',[
            '2 Oracle validators','2 Cortex + 16 CPU Daemons','Blueprint publisher + GPU Daemon','Local Console + GPU status page','23 containers / one host domain'])]
        for x,title,sub,lines in boxes:
            c.setFillColor(LIGHT);c.setStrokeColor(LINE);c.roundRect(x,y,bw,bh,8,fill=1,stroke=1)
            c.setFillColor(TEAL);c.setFont('Bold',10);c.drawString(x+12,y+bh-23,title)
            c.setFillColor(MUTED);c.setFont('Body',8);c.drawString(x+12,y+bh-41,sub)
            c.setFillColor(INK);c.setFont('Body',8.3)
            for i,line in enumerate(lines):c.drawString(x+12,y+bh-64-i*17,line)
        c.setStrokeColor(TEAL);c.setLineWidth(1.4);c.line(bw,126,bw+gap,126)
        for x,sign in ((bw,1),(bw+gap,-1)):
            c.line(x,126,x+sign*5,129);c.line(x,126,x+sign*5,123)
        c.setFont('Bold',8.5);c.setFillColor(TEAL)
        c.drawCentredString(CW/2,31,'Private SSH routes: consensus/control, artifact data, GPU discovery')
        c.setFont('Body',8);c.setFillColor(MUTED)
        c.drawCentredString(CW/2,13,'4 validators require 3 votes. A 2/2 site partition halts both sides.')


class Lineage(Flowable):
    def __init__(self):Flowable.__init__(self);self.width=CW;self.height=88
    def draw(self):
        c=self.canv;gap=10;bw=(CW-4*gap)/5
        labels=[('Source lock','Edition bytes'),('Token snapshot','Pinned tokenizer'),('Vector index','Frozen table'),('Replay claims','Two auditor keys'),('Blueprint','Parent + inputs')]
        for i,(a,b) in enumerate(labels):
            x=i*(bw+gap);c.setFillColor(LIGHT);c.setStrokeColor(LINE);c.roundRect(x,24,bw,53,6,fill=1)
            c.setFillColor(TEAL);c.setFont('Bold',8);c.drawCentredString(x+bw/2,56,a)
            c.setFillColor(INK);c.setFont('Body',7.3);c.drawCentredString(x+bw/2,41,b)
            if i<4:
                c.setStrokeColor(TEAL);c.line(x+bw,50,x+bw+gap,50)
                c.line(x+bw+gap,50,x+bw+gap-3,53)
        c.setFont('Body',8);c.setFillColor(MUTED)
        c.drawCentredString(CW/2,8,'Signed parents and content hashes connect the stages; dense vectors are reconstructed locally.')


def table(lines):
    rows=[[s.strip() for s in line.strip().strip('|').split('|')] for line in lines]
    rows=[r for r in rows if not all(re.fullmatch(r'[: -]+',s) for s in r)]
    count=len(rows[0]);widths=([.42,.58] if count==2 else [.26,.35,.39])
    if count==3 and rows[0][0] in ('Measurement','Mechanism'):widths=[.30,.35,.35]
    data=[[Paragraph(markup(s),styles['head' if i==0 else 'cell']) for s in row] for i,row in enumerate(rows)]
    t=Table(data,colWidths=[CW*x for x in widths],repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),TEAL),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[LIGHT,colors.white]),
        ('LINEBELOW',(0,-1),(-1,-1),.5,LINE)]))
    return t


def footer(c,doc):
    c.saveState();c.setStrokeColor(LINE);c.line(M,42,W-M,42)
    c.setFont('Body',7);c.setFillColor(MUTED)
    c.drawString(M,29,'HYPERTRIX  /  Hybrid research update  /  14 October 2026')
    c.drawRightString(W-M,29,f'{doc.page if doc.page<=2 else doc.page+2:02d}')
    if doc.page>1:
        c.setFillColor(TEAL);c.setFont('Bold',7.2);c.drawString(M,H-29,'ENGINEERING PILOT  |  EVIDENCE AND LIMITS')
    c.restoreState()


def main():
    gpu=json.loads((ROOT/'docs/results/hybrid-gpu-run-001/last-report.json').read_text())['body']
    assert gpu['accepted'] and gpu['chunks']==1807 and gpu['tokens']==2061330
    assert gpu['dense_bytes']==4*768*gpu['tokens'] and not gpu['training_executed']
    sections=(ROOT/'docs/hybrid_research_update.md').read_text(encoding='utf-8').split('<!-- page -->')
    story=[]
    for section_index,section in enumerate(sections):
        if section_index:story.append(PageBreak())
        else:
            story.append(Spacer(1,18));story.append(Paragraph('DAWL / TECHNICAL HANDOFF',styles['h2']))
        lines=section.strip().splitlines();i=0
        while i<len(lines):
            line=lines[i].strip()
            if not line:i+=1;continue
            if line=='<a id="evidence-guide"></a>':i+=1;continue
            if line.startswith('|'):
                group=[]
                while i<len(lines) and lines[i].strip().startswith('|'):group.append(lines[i]);i+=1
                story += [Spacer(1,3),table(group),Spacer(1,12)];continue
            if line=='{{topology}}':story.append(Topology());i+=1;continue
            if line=='{{lineage}}':story.append(Lineage());i+=1;continue
            if line.startswith('# '):
                style='title' if section_index==0 else 'h1'
                anchor='<a name="evidence-guide"/>' if line.startswith('# 9.') else ''
                story.append(Paragraph(anchor+markup(line[2:]),styles[style]));i+=1;continue
            if line.startswith('## '):story.append(Paragraph(markup(line[3:]),styles['h2']));i+=1;continue
            if lines[i].startswith('    '):
                story.append(Paragraph(markup(line),styles['code']));i+=1;continue
            parts=[line];i+=1
            while i<len(lines) and lines[i].strip() and not lines[i].startswith(('|','#','    ','{{')):
                parts.append(lines[i].strip());i+=1
            text=' '.join(parts)
            style='small' if text.startswith(('https://','Technical handoff','Microsoft:','CometBFT specification','NVIDIA:')) else 'body'
            story.append(Paragraph(markup(text),styles[style]))
    temp=ROOT/'tmp/pdfs';temp.mkdir(parents=True,exist_ok=True)
    body_path=temp/'hybrid-report-body.pdf'
    doc=SimpleDocTemplate(str(body_path),pagesize=A4,leftMargin=M,rightMargin=M,topMargin=49,bottomMargin=55,
        title='The Hypertrix: Hybrid architecture and experimental results',author='Espen',
        subject='Technical handoff for Yngvar; executed hybrid and GPU prototype experiments')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    build_architecture()
    embedded=build_architecture(temp/'architecture-embedded.pdf',page_start=3)
    writer=PdfWriter();writer.append(PdfReader(body_path));writer.merge(2,PdfReader(embedded))
    writer.add_metadata({'/Title':'The Hypertrix: hybrid architecture and experimental results',
                         '/Author':'Espen','/Subject':'Research handoff: architecture, methods, evidence and proposed hardening'})
    with OUT.open('wb') as stream:writer.write(stream)
    pdf=PdfReader(OUT)
    print(json.dumps(dict(path=str(OUT),pages=len(pdf.pages),page_characters=[len(p.extract_text()) for p in pdf.pages]),indent=2))


if __name__=='__main__':main()
