"""Render the Norwegian technical handoff; prose source stays editable in docs/."""
from pathlib import Path
import re
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Flowable,KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import matplotlib

ROOT=Path(__file__).resolve().parents[1]
fonts=Path(matplotlib.get_data_path())/'fonts/ttf'
for name,file in [('Body','DejaVuSans.ttf'),('Bold','DejaVuSans-Bold.ttf')]:pdfmetrics.registerFont(TTFont(name,str(fonts/file)))
pdfmetrics.registerFontFamily('Body',normal='Body',bold='Bold',italic='Body',boldItalic='Bold')
styles={
 'body':ParagraphStyle('body',fontName='Body',fontSize=8.8,leading=13.1,spaceAfter=8,textColor=colors.HexColor('#243840')),
 'title':ParagraphStyle('title',fontName='Bold',fontSize=24,leading=29,spaceAfter=15,textColor=colors.HexColor('#075e58')),
 'h2':ParagraphStyle('h2',fontName='Bold',fontSize=14,leading=19,spaceBefore=16,spaceAfter=9,keepWithNext=True,textColor=colors.HexColor('#075e58')),
}

class Architecture(Flowable):
    def __init__(self):super().__init__();self.width=499;self.height=207
    def draw(self):
        c=self.canv
        rows=[('Cortex + Blueprint','Kilder, Frags, tokenizer og datarekkefølge'),('Trenere + Daemons','Lokal backprop, signerte bidrag, replay og reduksjon'),('Oracle + konsument','Quorum, leases, vekthistorikk og kjørbar modell'),('Publiseringsport → Azure','Kontrollerte visningsdata; siste bilde ved nettverkssplitt')]
        for i,(title,detail) in enumerate(rows):
            y=163-i*48;c.setFillColor(colors.HexColor('#edf5f1'));c.setStrokeColor(colors.HexColor('#b8d4ca'));c.roundRect(0,y,499,41,6,fill=1,stroke=1)
            c.setFillColor(colors.HexColor('#075e58'));c.setFont('Bold',9);c.drawString(12,y+25,title)
            c.setFont('Body',8);c.setFillColor(colors.HexColor('#243840'));c.drawString(12,y+10,detail)
        c.setFont('Body',7.5);c.drawString(0,0,'Neste steg: WAN-sikkerhet, uavhengige aktører, optimizer-state og heterogene kontroller.')

def inline(text):
    text=escape(text)
    text=re.sub(r'\*\*(.*?)\*\*',r'<b>\1</b>',text)
    text=re.sub(r'`(.*?)`',lambda m:'<font size="7.4">'+m.group(1)+'</font>',text)
    return text

story=[];buffer=[];fenced=False
def flush():
    if buffer:story.append(Paragraph(inline(' '.join(buffer)),styles['body']));buffer.clear()
for line in (ROOT/'docs/research-handoff-2026-10-14.md').read_text(encoding='utf-8').splitlines():
    if line.startswith('```'):
        flush();fenced=not fenced
        if fenced:story.extend([Architecture(),Spacer(1,10)])
        continue
    if fenced:continue
    if line.startswith('# '):flush();story.append(Paragraph(inline(line[2:]),styles['title']))
    elif line.startswith('## '):flush();story.append(Paragraph(inline(line[3:]),styles['h2']))
    elif not line.strip():flush()
    elif re.match(r'^(\d+\. |\- )',line):flush();buffer.append(line)
    else:buffer.append(line)
flush()
def page(canvas,doc):
    canvas.setFont('Body',8);canvas.setFillColor(colors.HexColor('#62777a'))
    canvas.drawString(48,25,'HYPERTRIX  /  Teknisk forskningsnotat  /  14.10.2026')
    canvas.drawRightString(A4[0]-48,25,str(doc.page))
dest=ROOT/'output/pdf/Hypertrix_Yngvar_2026-10-14.pdf';dest.parent.mkdir(parents=True,exist_ok=True)
SimpleDocTemplate(str(dest),pagesize=A4,leftMargin=48,rightMargin=48,topMargin=42,bottomMargin=45,title='Hypertrix: teknisk status og første deltakerpilot',author='Hypertrix project').build(story,onFirstPage=page,onLaterPages=page)
print(dest)
