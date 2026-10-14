"""Read-only Hypertrix terminal console; refresh, inspect and retain last-known state."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
import sys
import time
import httpx
from rich.console import Console,Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from network_view import view,clean,ROLES


def amount(value,units=('B','KB','MB','GB','TB')):
    if value is None:return 'Not reported'
    for u in units:
        if abs(value)<1000 or u==units[-1]:return f'{value:,.1f} {u}'
        value/=1000


def work_rows(model):
    rows=[];seen=set()
    activities=sorted(model.get('activities',[]),key=lambda a:(bool(a.get('live')),a.get('observed_at','')),reverse=True)
    for a in activities:
        identity=(a.get('job'),a.get('role'))
        if not a.get('live') and identity in seen:continue
        seen.add(identity)
        rate=f"{a['targets_per_second']:,.0f} tok/s" if a.get('targets_per_second') is not None else ''
        if a.get('loss') is not None:rate+=f"  loss {a['loss']:.3f}"
        rows.append((Text(clean(a.get('job','Work'))),f"{a.get('completed',0):,} / {a.get('total',0):,} {a.get('unit','')}",rate,Text(clean(a.get('phase','Unknown')))))
    for i,s in enumerate(model['stages']):rows.append((f'Oracle stage {i+1}',f"{s.get('round',0)} / {s.get('body',{}).get('rounds','?')} rounds",'',Text(clean(s.get('status','Unknown')))))
    return rows


def render(model,tab='network',page=0,height=36,paused=False,error=None):
    status='PAUSED' if paused else 'DISCONNECTED' if error else model['health'].upper()
    color='yellow' if paused or error or not model['fresh'] else 'green'
    heading=Text('HYPERTRIX  /  THE COLLECTIVE',style='bold cyan');heading.append('    '+status,style=color)
    summary=Text(f"Ledger {model['height']:,}  |  {len(model['nodes'])} {'observed nodes' if model['mode']=='observer' else 'members'}  |  {model['work'].get('accepted_contributions',0)} contributions  |  {model['work'].get('checkpoints',0)} checkpoints")
    m=model['metrics'];gpu=model['gpu'] or {}
    metric=Text('RX '+amount(m.get('rx_bytes_per_second'))+('/s' if m.get('rx_bytes_per_second') is not None else '')+'   TX '+amount(m.get('tx_bytes_per_second'))+('/s' if m.get('tx_bytes_per_second') is not None else ''))
    flop=amount(m.get('flops_per_second'),('FLOP/s','kFLOP/s','MFLOP/s','GFLOP/s','TFLOP/s'))
    metric.append('   Compute '+flop+(' ('+m['compute_kind']+')' if m.get('flops_per_second') is not None else ''))
    cpu=m.get('cpu_cores');metric.append('   CPU '+(f'{cpu:.2f} cores' if cpu is not None else '—'))
    activities=model.get('activities',[])
    active=[a for a in activities if a.get('live') and a.get('status')=='running']
    live=Text('GPU '+str(gpu.get('utilization_percent','—'))+'%')
    if gpu.get('memory_used_mib') is not None:live.append(f"  VRAM {gpu['memory_used_mib']/1024:.1f} / {gpu['memory_total_mib']/1024:.0f} GiB")
    for a in active:
        live.append('  |  '+clean(a.get('phase','Working'))+f" {a.get('completed',0):,}/{a.get('total',0):,}",style='bold green')
        if a.get('targets_per_second') is not None:live.append(f"  {a['targets_per_second']:,.0f} tokens/s")
        if a.get('loss') is not None:live.append(f"  loss {a['loss']:.3f}")
        if a.get('epoch') is not None:live.append(f"  epoch {a['epoch']:.2f}/{a.get('epochs','?')}")
    header=Panel(Group(heading,summary,metric,live),border_style=color)
    table=Table(expand=True,box=None,header_style='bold cyan',padding=(0,1))
    page_size=max(2,height-13)
    if tab in ('network','history'):
        items=model['nodes'] if tab=='network' else model['history']
        for name in ('Node','Role','State','Identity'):table.add_column(name)
        selected=items[page*page_size:(page+1)*page_size]
        for n in selected:table.add_row(Text(clean(n['name'])),Text(ROLES.get(n['role'],clean(n['role']))),Text(clean(n['status']),style='green' if n.get('running') else 'white'),Text(clean(n['identity'])[:12],style='dim'))
        if not selected:table.add_row('No nodes in this view','','','')
        caption=f'{len(items)} nodes · page {page+1} / {max(1,(len(items)+page_size-1)//page_size)}'
    elif tab=='work':
        for name in ('Work','Progress','Throughput / loss','State'):table.add_column(name)
        rows=work_rows(model)
        for row in rows[page*page_size:(page+1)*page_size]:table.add_row(*row)
        if not rows:table.add_row('No work reported','','','')
        caption=f"Page {page+1} / {max(1,(len(rows)+page_size-1)//page_size)} · {model['work'].get('pending_jobs',0)} pending jobs · {model['work'].get('checkpoint_receipts',0)} checkpoint receipts"
    else:
        table.add_column('Signal');table.add_column('Current value')
        for key,value in [('Verification',model['verification']),('Genesis',model['genesis']),('Chain',model['chain']),
                          ('GPU',gpu.get('name','Not reported')),('GPU utilization',str(gpu.get('utilization_percent','—'))+'%'),
                          ('Memory',amount(m.get('memory_bytes'))),('Compute coverage',f"{m.get('compute_nodes',0)} / {m.get('active_nodes',len(model['nodes']))} reporting nodes"),
                          ('I/O scope',m.get('network_scope','No operational measurements in this publication.'))]:
            table.add_row(Text(key),Text(clean(value)))
        caption='Measurements and verification details'
    body=Panel(table,title=tab.title(),subtitle=Text(caption),border_style='blue')
    warning=Text('Connection lost; retaining the last observation.' if error else ('Display paused; incoming samples are not being fetched.' if paused else ''),style='yellow')
    if model['age'] is not None:warning.append(f"  Sample age {max(0,model['age']):.0f}s",style='dim')
    controls=Text('1 Network  2 Work  3 Verification  4 History  |  J/K Pages  P Pause  R Refresh  Q Quit',style='dim')
    return Group(header,body,warning,controls)


@contextmanager
def keys():
    if os.name=='nt':
        import msvcrt
        yield lambda:msvcrt.getwch().lower() if msvcrt.kbhit() else None
    else:
        import select,termios,tty
        fd=sys.stdin.fileno();saved=termios.tcgetattr(fd);tty.setcbreak(fd)
        try:yield lambda:sys.stdin.read(1).lower() if select.select([sys.stdin],[],[],0)[0] else None
        finally:termios.tcsetattr(fd,termios.TCSADRAIN,saved)


def fetch(endpoint):
    with httpx.Client(timeout=5,trust_env=False,follow_redirects=False) as client:
        response=client.get(endpoint);response.raise_for_status();payload=response.json();view(payload);return payload


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--endpoint',default='http://127.0.0.1:8882/api/status')
    p.add_argument('--interval',type=float,default=3);p.add_argument('--once',action='store_true');p.add_argument('--json',action='store_true')
    p.add_argument('--view',choices=['network','work','verification','history'],default='network');p.add_argument('--duration',type=float)
    a=p.parse_args();console=Console(legacy_windows=False)
    if a.interval<1:p.error('Refresh interval must be at least one second')
    if a.duration is not None and a.duration<=0:p.error('Duration must be positive')
    if not a.endpoint.startswith(('http://','https://')):p.error('Use an HTTP(S) monitor endpoint')
    if a.once or a.json or not sys.stdin.isatty():
        try:model=view(fetch(a.endpoint))
        except (httpx.HTTPError,ValueError,KeyError,TypeError) as e:console.print('Monitor unavailable ('+type(e).__name__+').',style='yellow');return 1
        if a.json:print(json.dumps({k:v for k,v in model.items() if k not in ('nodes','history')},default=str))
        else:console.print(render(model,a.view,height=max(40,console.height)))
        return 0
    empty={'health':'Connecting','fresh':False,'height':0,'age':None,'nodes':[],'history':[],'work':{},'metrics':{},'gpu':None,'stages':[],'mode':'observer','chain':'','genesis':'','verification':'Waiting for observer'}
    start=time.monotonic();next_fetch=0;future=None;payload=None;paused=False;tab=a.view;page=0;error=None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, keys() as key, Live(console=console,screen=True,auto_refresh=False) as live:
            while a.duration is None or time.monotonic()-start<a.duration:
                pressed=key()
                if pressed in ('q','\x03'):break
                if pressed in ('1','2','3','4'):tab={'1':'network','2':'work','3':'verification','4':'history'}[pressed];page=0
                if pressed=='p':paused=not paused;next_fetch=0
                if pressed=='r':next_fetch=0
                if pressed=='j':page+=1
                if pressed=='k':page=max(0,page-1)
                if future and future.done():
                    try:
                        result=future.result()
                        if not paused:payload=result;error=None
                    except (httpx.HTTPError,ValueError,KeyError,TypeError) as e:error=type(e).__name__
                    future=None;next_fetch=time.monotonic()+a.interval
                if not paused and future is None and time.monotonic()>=next_fetch:future=pool.submit(fetch,a.endpoint)
                model=view(payload) if payload else empty
                if error:model={**model,'fresh':False,'metrics':{},'gpu':None,'activities':[{**a,'live':False,'targets_per_second':None,'phase':'Last seen'} for a in model.get('activities',[])],'nodes':[{**n,'running':False,'status':'Last seen'} for n in model['nodes']]}
                count=len(work_rows(model)) if tab=='work' else len(model['nodes'] if tab=='network' else model['history']);page=min(page,max(0,(count-1)//max(2,console.height-13)))
                live.update(render(model,tab,page,console.height,paused,error),refresh=True);time.sleep(.1)
    except KeyboardInterrupt:pass
    console.print('Hypertrix Console closed.',style='dim');return 0


if __name__=='__main__':raise SystemExit(main())
