"""Plot observed validation loss; no synthetic or interpolated measurements."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
names=[name for name in ('dense','moe') if (a.root/name/'baseline.json').exists()]
fig,axes=plt.subplots(1,len(names),figsize=(7*len(names),4.5),squeeze=False,layout='constrained')
colors={'code':'#166b9c','economics':'#a04c1c','literature':'#276b42'}
for axis,name in zip(axes[0],names):
    root=a.root/name
    baseline=json.loads((root/'baseline.json').read_bytes())['validation']
    checkpoints=sorted((json.loads(p.read_bytes()) for p in (root/'checkpoints').glob('*/checkpoint.json')),key=lambda d:d['step'])
    rows=[(0,baseline)]+[(d['step'],d['evaluation']) for d in checkpoints if d.get('evaluation')]
    for domain,color in colors.items():
        points=[(step,value[domain]['nll']) for step,value in rows if domain in value]
        axis.plot([x for x,y in points],[y for x,y in points],marker='.',label=domain.capitalize(),color=color)
    axis.set_title('Dense · random initialization' if name=='dense' else 'MoE · continuation of selected dense checkpoint')
    axis.set_xlabel('Optimizer updates within this run');axis.set_ylabel('Validation negative log likelihood (nats / token)')
    axis.grid(alpha=.2);axis.legend(frameon=False)
fig.suptitle('Hypertrix Seed — fixed held-out windows',fontsize=15)
a.output.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.output,dpi=160);plt.close(fig)
print(a.output)
