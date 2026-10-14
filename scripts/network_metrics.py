"""Rate accounting for observer samples. Missing/reset counters never imply zero work."""
from datetime import datetime,timezone
import math
from pathlib import Path
import re


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0


def quantity(value):
    match=re.fullmatch(r'\s*([\d.]+)\s*([KMGT]?i?B)\s*',str(value),re.I)
    if not match:return None
    units={'b':1,'kb':1000,'mb':1000**2,'gb':1000**3,'tb':1000**4,
           'kib':1024,'mib':1024**2,'gib':1024**3,'tib':1024**4}
    return float(match[1])*units[match[2].lower()]


def cpu_seconds(value):
    units={'h':3600,'m':60,'s':1,'ms':.001,'us':.000001,'µs':.000001,'ns':1e-9}
    parts=re.findall(r'([\d.]+)(ns|us|µs|ms|h|m|s)',str(value))
    return sum(float(v)*units[u] for v,u in parts) if parts else None


def namespace_counters(containers):
    """Count each shared network namespace once, including its loopback traffic."""
    values={}
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    for c in containers:
        if not c['State']['Running']:continue
        pid=c['State'].get('Pid')
        if not pid:continue
        try:
            ns=boot+':'+Path(f'/proc/{pid}/ns/net').readlink().as_posix()
            if ns in values:continue
            rows=Path(f'/proc/{pid}/net/dev').read_text().splitlines()[2:]
            rx=tx=0
            for row in rows:
                fields=row.split(':',1)[1].split();rx+=int(fields[0]);tx+=int(fields[8])
            values[ns]={'rx_bytes':rx,'tx_bytes':tx}
        except (OSError,ValueError,IndexError):continue
    return values


def compute_report(value,now):
    """Optional worker instrumentation: cumulative FLOPs, fresh clock and counter epoch."""
    try:
        age=(now-datetime.fromisoformat(value['observed_at'])).total_seconds()
        valid=(value['schema']=='hypertrix.node-compute/1' and -5<=age<=30
               and value['convention']=='scalar-fp-ops-fma2'
               and value['kind'] in ('counted','estimated') and isinstance(value['counter_epoch'],str)
               and bool(value['counter_epoch']) and number(value['flops_total']))
        return value if valid else None
    except (KeyError,TypeError,ValueError):return None


def aggregate(current,previous=None):
    now=datetime.fromisoformat(current['observed_at'])
    elapsed=(now-datetime.fromisoformat(previous['observed_at'])).total_seconds() if previous else 0
    usable=0<elapsed<=60
    old={n['id']:n for n in previous.get('nodes',[])} if previous else {}
    active=[n for n in current.get('nodes',[]) if n.get('running') and not n.get('historical_attempt')]
    cpu=0;cpu_covered=0;memory=0;memory_covered=0;compute={'counted':0.,'estimated':0.};flop_covered=0;kinds=set()
    for n in active:
        r=n.get('stats') or {};p=(old.get(n['id']) or {}).get('stats') or {}
        seconds=cpu_seconds(r.get('cpu_time'));before=cpu_seconds(p.get('cpu_time'))
        if usable and seconds is not None and before is not None and seconds>=before:
            cpu+=(seconds-before)/elapsed;cpu_covered+=1
        mem=quantity(str(r.get('mem_usage','')).split('/')[0])
        if mem is not None:memory+=mem;memory_covered+=1
        report=compute_report(n.get('compute') or {},now)
        prior=compute_report((old.get(n['id']) or {}).get('compute') or {},now)
        if usable and report and prior and report['counter_epoch']==prior['counter_epoch'] and report['kind']==prior['kind']:
            dt=(datetime.fromisoformat(report['observed_at'])-datetime.fromisoformat(prior['observed_at'])).total_seconds()
            if dt>0 and report['flops_total']>=prior['flops_total']:
                compute[report['kind']]+=(report['flops_total']-prior['flops_total'])/dt;flop_covered+=1;kinds.add(report['kind'])
    rx=tx=0.;ns_covered=0
    for ns,counters in current.get('network_namespaces',{}).items():
        before=(previous or {}).get('network_namespaces',{}).get(ns)
        if usable and before and all(counters[k]>=before[k] for k in ('rx_bytes','tx_bytes')):
            rx+=(counters['rx_bytes']-before['rx_bytes'])/elapsed;tx+=(counters['tx_bytes']-before['tx_bytes'])/elapsed;ns_covered+=1
    return {'schema':'hypertrix.network-metrics/1','observed_at':current['observed_at'],
            'sample_seconds':elapsed if usable else None,'active_nodes':len(active),
            'cpu_cores':cpu if cpu_covered else None,'cpu_nodes':cpu_covered,
            'memory_bytes':memory if memory_covered else None,'memory_nodes':memory_covered,
            'rx_bytes_per_second':rx if ns_covered else None,'tx_bytes_per_second':tx if ns_covered else None,
            'network_namespaces':ns_covered,'network_scope':'Observed namespace I/O, including loopback; shared namespaces may include other processes. RX and TX are separate directions, not summed unique payload.',
            'flops_per_second':sum(compute.values()) if flop_covered else None,'compute_nodes':flop_covered,
            'compute_kind':'mixed' if len(kinds)>1 else next(iter(kinds)) if kinds else 'unreported',
            'compute_by_kind':compute,'scope':'Observer measurements; resource use is not a consensus or numerical-verification claim.'}
