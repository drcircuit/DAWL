"""Record the supervised fail-fast replay outcome without replacing raw checks."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from dawl_finetune.data import file_hash
from dawl_node.common import read_json,write_json
root=Path('/pilot')
checks=[read_json(p) for p in sorted(root.glob('replay-checkpoint-*.json'))]
if not checks or all(c['exact_match'] for c in checks):raise ValueError('This recorder requires a captured mismatch')
initialization=read_json(root/'replay-initialization-diagnostic.json')
kernel=read_json(root/'replay-kernel-diagnostic.json')
result={'schema':'hypertrix.local-full-training-replay/1','status':'mismatch','checks':checks,
        'completed_utc':datetime.now(timezone.utc).isoformat(),
        'wall_seconds':read_json(root/'replay-progress.json')['elapsed_seconds'],
        'planned_checkpoints':len(read_json(root/'training-summary.json')['epochs']),
        'completed_checkpoint_comparisons':len(checks),
        'termination':'Operator stopped the first replay after a nonmatching epoch-1 boundary to investigate within the original GPU deadline. Podman escalated SIGTERM to SIGKILL; exit 137 was intentional termination, not a successful verifier exit.',
        'recipe_sha256':file_hash(root/'recipe.json'),'dataset_lock_sha256':file_hash(root/'dataset-lock.json'),
        'verifier_script_sha256':file_hash(root/'verifier-replay.py'),'gpu':read_json(root/'gpu-session.json')['gpu'],
        'initialization_diagnostic_sha256':file_hash(root/'replay-initialization-diagnostic.json'),
        'kernel_diagnostic_sha256':file_hash(root/'replay-kernel-diagnostic.json'),
        'initialization_matched':initialization['full_vs_abbreviated_identical'],
        'paired_prefix_exact_tensors':kernel['final_exact_tensors'],'paired_prefix_tensor_count':kernel['final_tensor_count'],
        'recorder_script_sha256':file_hash(Path(__file__)),
        'scope':'A fresh-process attempt replayed the first full epoch and compared every adapter tensor. Exact equality failed. Later checkpoint boundaries were not compared. Initialization and paired-prefix diagnostics are separate receipts. No exact-training pass or Oracle certificate is claimed.'}
write_json(root/'replay-verification.json',result);print(json.dumps(result,indent=2))
