# Hypertrix home lab — Windows / WSL2

Copy the release ZIP and matching `hypertrix-lab-runtime-004.oci.tar.gz` to each
machine. Extract the ZIP outside OneDrive. Its manifest pins every code file and
the OCI runtime. Model/data packages travel separately, without operator keys.

## First launch

Use a current NVIDIA **Windows** driver and WSL2 Linux distribution. CUDA is
provided through the Windows driver; do not install a Linux display driver in
WSL. See [NVIDIA's WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).
The kit imports its exact Python/PyTorch/CUDA runtime, so you do not need a host
Python or CUDA toolkit.

From PowerShell in the extracted kit:

```powershell
wsl --list --verbose
.\lab\Quickstart.ps1 -Distribution Ubuntu -NodeName ada6000 -Install `
  -ImageArchive 'C:\Hypertrix\hypertrix-lab-runtime-004.oci.tar.gz'
```

Choose your installed distribution name and use `rtx4090` on the other machine.
`-Install` installs Podman, Python3 and CA certificates in that WSL distribution
if needed. The doctor runs a real forward/backward test on the NVIDIA GPU and
writes `/var/lib/dawl/home-lab/doctor.json`. `-Cpu` selects CPU diagnostics.
Files are verified and copied into native WSL storage; large arrays, compiler
caches and checkpoints do not train from `/mnt/c` or a synced directory.

The launch command does not alter Windows firewall, the saved power plan or `.wslconfig`.
Foreground training/transfer commands hold a temporary idle-sleep request until
the command exits. Keep the machines powered; manual sleep/lid actions can still
interrupt work. WSL needs sufficient RAM;
32 GB allocated to WSL is a reasonable starting point for these machines, with
at least 50 GB free for runtime, corpus, checkpoints and replicas.

For later commands, a short wrapper avoids repeating machine settings:

```powershell
function Lab {
  param([string]$Action, [string[]]$Task=@(), [switch]$Cpu)
  .\lab\Quickstart.ps1 -Distribution Ubuntu -NodeName ada6000 `
    -Action $Action -Task $Task -Cpu:$Cpu
}
Lab DdpTest -Cpu
Lab Status
```

`DdpTest` exercises two actual CPU processes and gradient exchange. Its destination
must be new; the test never overwrites prior evidence. Services are separate
containers. `Lab Stop -Cpu` stops the provider, discovery and studio containers;
it does not delete their data or interrupt a separately launched trainer.

## Join the private LAN and fetch a checkpoint

Use the same allowed subnet and printed network ID on every peer. Replace the
example subnet and addresses with your LAN. Discovery and artifact services use
TCP 8913 and 8911; the optional studio uses TCP 8887.

```powershell
Lab Network -Cpu -Task @('--cidr','192.168.1.0/24')
Lab Mesh -Cpu -Task @('--network','/lab/network.json','--network-id','NETWORK_ID',
  '--advertise','192.168.1.42','--bind','0.0.0.0',
  '--roles','TRAINER,TRAINING_VERIFIER',
  '--seeds','http://192.168.1.40:8913')
Lab Fetch -Cpu -Task @('--url','http://192.168.1.40:8911',
  '--package-id','PACKAGE_ID','--publisher','PUBLISHER_PUBLIC_KEY')
Lab Run -Cpu -Task @('-m','dawl_lab.artifacts','verify','--root','/lab/incoming',
  '--publisher','PUBLISHER_PUBLIC_KEY','--package-id','PACKAGE_ID')
```

The actual package ID, publisher key and checkpoint step come from the publishing
machine's `publish` result. Obtain those pins through a trusted channel. A retry
resumes partial object downloads. Existing files with different hashes are
rejected; use another `-Storage` root for another experiment.

For an interval listed in the package's `replay_intervals`, a verifier with the
recorded hardware/runtime can replay the **public** package:

```powershell
Lab Audit -Task @('--start','REPLAY_START','--end','CHECKPOINT_STEP',
  '--expected-operator','MODEL_OPERATOR_KEY')
```

This runs without network access, mounts the received package read only, and
mounts a separate output directory for the verifier's own key and signed receipt.
The trainer's private key is not supplied. The release contains reviewed training
source snapshots; the auditor selects the matching pinned implementation and
never executes arbitrary Python downloaded from a peer. `-Cpu` applies to a
checkpoint whose recorded runtime is CPU. The 4090/6000 Ada should first train
their own branches: the 5090 recipe's strict replay check intentionally rejects
a different GPU cohort.

WSL's default NAT does not expose these services to other computers automatically.
On supported Windows 11 installations, mirrored networking supports direct LAN
access. Otherwise configure Windows TCP port proxies to the current WSL address.
Open only the required ports to the home subnet in Windows/Hyper-V firewall rules.
Use [Microsoft's WSL networking instructions](https://learn.microsoft.com/en-us/windows/wsl/networking)
for the appropriate mode; changing the mode restarts WSL, so do it before jobs.

The lab's HTTP discovery/transfer paths are for this private network. The CLI
pins identities and verifies bytes; it does not provide transport confidentiality.
Do not expose the lab ports to the public Internet.

## Continue a published model on your GPU

Every machine creates its own branch key. This command copies the parent's weights
and AdamW state, starts a new measured phase, and binds its lineage to the original
checkpoint. It does not need or copy the publisher's private key.

```powershell
Lab Run -Task @('-m','dawl_lab.run','fork','--root','/lab/run',
  '--dataset','/lab/incoming/dataset','--parent','/lab/incoming/run',
  '--parent-step','CHECKPOINT_STEP','--expected-parent-key','MODEL_OPERATOR_KEY',
  '--steps','1000','--batch-size','96','--micro-batch','16',
  '--checkpoint-every','100','--compile')
Lab Run -Task @('-m','dawl_lab.run','train','--root','/lab/run',
  '--dataset','/lab/incoming/dataset')
```

The doctor must pass first. Microbatch 16 is the conservative starting point for
unbenchmarked GPUs. Measure before choosing 32 or larger. The global batch can
remain 96 while the microbatch changes, but floating-point accumulation order
then changes: pin that choice in a new recipe. Use `--resume` to continue an
interrupted phase from its latest signed checkpoint with the original recipe.

For a fresh random-initialization experiment use `create` instead of `fork`, omit
the parent arguments and retain the same dataset. `--layers`, `--width`,
`--heads`, `--kv-heads` and `--hidden` select the decoder configuration at creation.

After the run, select an interval bounded by saved checkpoints:

```powershell
Lab Run -Task @('-m','dawl_lab.run','replay','--root','/lab/run',
  '--dataset','/lab/incoming/dataset','--start','0','--end','100')
Lab Run -Cpu -Task @('-m','dawl_lab.verify','run','--root','/lab/run',
  '--dataset','/lab/incoming/dataset','--output','/lab/artifact-verification.json')
Lab Studio -Cpu -Task @('--dataset','/lab/incoming/dataset')
```

Open **http://localhost:8887/**. The studio loads the best validated checkpoint,
uses CPU inference independently of the trainer and records no prompts. Exact
replay checks every model tensor and AdamW tensor over the stated interval;
cross-GPU equality must be measured rather than inferred from matching hashes.

Replay and training append to the same lineage: run them sequentially for one
branch. You can run different branches concurrently in separate storage roots.

## Publish, replicate and serve

```powershell
Lab Run -Cpu -Task @('-m','dawl_lab.artifacts','publish','--root','/lab/provider',
  '--dataset','/lab/incoming/dataset','--run','/lab/run','--step','CHECKPOINT_STEP')
Lab Provider -Cpu -Task @('--bind','0.0.0.0')
```

To serve the unchanged package you fetched, use `replicate --root /lab/provider
--bundle /lab/incoming --publisher PUBLISHER_PUBLIC_KEY` through `Lab Run -Cpu`,
then start the provider. The original package signature/ID survives replication.
The package includes the selected checkpoint, optimizer, available replay boundary
checkpoints and receipts, initialization, tokenizer, token arrays, source metadata,
batch schedule and frozen training code. Raw source texts remain
in the curator's content store; their origin URLs and content hashes are public
in `sources.json`. Source retokenization additionally needs that source store.

## Optional joint training with DDP

This is synchronous gradient sharing, with one complete model and optimizer per
GPU. All ranks need the same public parent package. Set up bidirectional routable
LAN addresses first; a port proxy for the rendezvous port alone is insufficient
for NCCL's additional peer connections. Use explicit peer-scoped firewall rules,
not an Internet-facing rendezvous. The three-machine CUDA deployment must be
tested after the CPU collective test.

On each machine, use a different `--node-rank` (`0`, `1`, `2`) and the same master:

```powershell
Lab Ddp -Task @('-m','torch.distributed.run','--nnodes','3','--nproc-per-node','1',
  '--node-rank','0','--master-addr','192.168.1.40','--master-port','29500',
  '-m','dawl_lab.ddp','--parent','/lab/incoming/run',
  '--dataset','/lab/incoming/dataset','--root','/lab/ddp-run',
  '--start','CHECKPOINT_STEP','--end','LATER_STEP',
  '--expected-parent-key','MODEL_OPERATOR_KEY','--micro-batch','16')
```

The parent recipe must contain the requested future steps and a global batch
divisible by three. DDP uses that already frozen schedule, verifies participant
agreement and saves rank receipts. The training model is eager for this first
distributed implementation, with microbatch 16 by default. It does not inherit a
larger compiled microbatch that might exceed memory in eager mode. `no_sync()` accumulates gradients locally until the
final microbatch. If a rank disappears, the collective fails instead of silently
publishing partial work. No independent replay certificate is implied by rank
agreement. For now, independent branches are the simplest way to use differing
GPUs and unreliable home-node uptime.

## Evidence and troubleshooting

* `doctor.json`: actual hardware/runtime and forward/backward result.
* `run/recipe.json`, `schedule.json`, `implementation.json`: frozen experiment.
* `run/lineage.jsonl`: signed initialization, checkpoints and result receipts.
* `run/resources-*.jsonl`: sampled resource usage, committed by result hashes.
* `run/replay-*.json`: tensor/optimizer comparison for a stated interval.
* `incoming/package.json`: signed publication manifest; transfer receipts include bytes and resume offsets.
* `peer/dht-status.json`: discovery contacts and counters.

`Lab Status -Cpu` shows containers created for the node name. `podman logs NAME`
inside WSL reads a service or worker log. Large artifacts live beneath
`/var/lib/dawl/home-lab`; use `-Storage` consistently to create isolated experiments.
Hardware limits, batch sizes, runtime changes and new corpora should produce new
recipes rather than rewriting accepted evidence.
