"""Publication figures from signed checkpoint descriptors and paired evaluation."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

p=argparse.ArgumentParser();p.add_argument('--release',type=Path,required=True);a=p.parse_args();root=a.release
report=json.loads((root/'report.json').read_bytes());comparison=json.loads((root/'comparison.json').read_bytes())
descriptors=[json.loads(p.read_bytes()) for p in sorted((root/'dense/checkpoints').glob('*/checkpoint.json'))]
baseline=json.loads((root/'dense/baseline.json').read_bytes())['validation']
points=[(0,baseline)]+[(d['step'],d['evaluation']) for d in descriptors if d['evaluation']]
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(1,2,figsize=(12.5,4.8),layout='constrained')
colors={'code':'#347bb4','economics':'#bd7d28','literature':'#6650a2','norwegian':'#168a78'}
for domain,color in colors.items():axes[0].plot([x for x,y in points],[y[domain]['nll'] for x,y in points],label=domain.title(),color=color,linewidth=2)
axes[0].axvline(report['summary']['selected_step'],color='#333333',ls=':',lw=1,label='Selected checkpoint')
axes[0].set(xlabel='Optimizer updates',ylabel='Sampled validation NLL (lower is better)',title='Seed II · checkpoint learning curve');axes[0].legend(frameon=False,fontsize=8);axes[0].grid(alpha=.18)
domains=list(colors);x=np.arange(len(domains));width=.25
labels={'seed1_dense':'Seed I dense · 5.8M','seed1_moe':'Seed I MoE · 10.5M','seed2_dense':'Seed II dense · 40.6M'}
for i,(name,row) in enumerate(comparison['models'].items()):
    axes[1].bar(x+(i-1)*width,[row['test'][d]['nll'] for d in domains],width,label=labels[name],color=['#9dafc1','#b8abc8','#168a78'][i])
axes[1].set(xticks=x,xticklabels=[d.title() for d in domains],ylabel='Sampled test NLL (lower is better)',title='Same tokenizer and held-out windows');axes[1].legend(frameon=False,fontsize=8);axes[1].grid(axis='y',alpha=.18);axes[1].set_axisbelow(True)
fig.suptitle('Hypertrix Seed · corpus and model growth',fontsize=16,weight='bold')
fig.savefig(root/'learning.png',dpi=180);fig.savefig(root/'learning.pdf');plt.close(fig)
