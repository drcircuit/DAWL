"""Package the technical brief with public archived evidence and its editable builder.

Usage: python scripts/package_research_brief.py --research-root PATH
This packages existing results; it does not run platform, training or human experiments.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import zipfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--research-root", type=Path, required=True)
args = parser.parse_args()
research = args.research_root.resolve()
pdf = ROOT / "output/pdf/DAWL_technical_research_brief.pdf"
assert pdf.is_file(), "Build and visually review the PDF before packaging"
items = {}

def add(path, name):
    assert path.is_file(), path
    payload = path.read_bytes()
    for key_type in (b"PRIVATE KEY", b"OPENSSH PRIVATE KEY"):
        assert b"-----BEGIN " + key_type + b"-----" not in payload, path
    assert name not in items, name
    items[name] = payload

add(pdf, "DAWL_technical_research_brief.pdf")
for name in ["platform.md", "platform_run_001.md", "swarm.md", "swarm_run_001.md"]:
    add(ROOT/"docs"/name, "docs/"+name)
for directory in ["platform-run-001", "swarm-run-001"]:
    for path in sorted((ROOT/"docs/results"/directory).iterdir()):
        add(path, "docs/results/"+directory+"/"+path.name)
for name in ["build_research_brief.py", "package_research_brief.py"]:
    add(ROOT/"scripts"/name, "scripts/"+name)
add(research/"research/experiments/R001-protocol.md", "research/experiments/R001-protocol.md")
for name in ["summary.json", "trials.jsonl"]:
    add(research/"artifacts/research/R001-run-001"/name, "artifacts/research/R001-run-001/"+name)

# Verify the source archives and the plotted observations, without rerunning experiments.
platform = json.loads(items["docs/results/platform-run-001/summary.json"])
for name, expected in platform["evidence_hashes"].items():
    assert hashlib.sha256(items["docs/results/platform-run-001/"+name]).hexdigest() == expected, name
assert platform["tokens"] == 1657163
assert platform["dense_bytes_reconstructed"] == 4*platform["tokens"]*768
assert round(platform["package_reduction_vs_dense_percent"],2) == 96.68
r001 = json.loads(items["artifacts/research/R001-run-001/summary.json"])
trials = items["artifacts/research/R001-run-001/trials.jsonl"]
assert hashlib.sha256(trials).hexdigest() == r001["trials_sha256"]
assert sum(c["trials"] for c in r001["cells"]) == len(trials.splitlines()) == 1803
expected_detections = {1: [1,34,77], 4: [20,111,172]}
for m, expected in expected_detections.items():
    for k, count in zip([1,8,16], expected):
        cell = next(c for c in r001["cells"] if c["bad_chunks"] == m and c["samples"] == k)
        exact = 1 - math.comb(43-m,k)/math.comb(43,k)
        assert cell["detections"] == count and cell["trials"] == 200
        assert abs(exact-cell["exact_detection_probability"]) < 1e-14
assert r001["oracle_mismatches"] == 0
verification = json.loads(items["docs/results/swarm-run-001/observation-verification.json"])
assert verification["signed_receipts"] == 40 and verification["signed_batches"] == 39
assert verification["all_five_converged"] is True

items["READ_ME_FIRST.txt"] = (
    "DAWL technical research handoff for Yngvar\n"
    "Prepared 13 October 2026\n\n"
    "Start with DAWL_technical_research_brief.pdf (13 pages).\n"
    "The docs directory contains existing architecture/runbooks and public execution evidence.\n"
    "The research protocol and R001 summary/raw trials are copied from the separate research branch.\n"
    "SHA256SUMS.json identifies the exact bytes in this bundle; it is a transport integrity\n"
    "manifest, not an externally authenticated trust anchor. Signed claims still require\n"
    "the intended independently obtained policy pins and correct verifier software.\n\n"
    "This is a documentation and evidence bundle, not a self-contained platform deployment.\n"
    "It excludes private keys, full source/model artifacts, Python environments and live state.\n"
    "The repository and pinned large artifacts are needed to reproduce platform execution.\n"
    "Some original logs contain machine-local paths; these are retained as historical evidence.\n\n"
    "Editable document source: scripts/build_research_brief.py. It contains the prose, tables,\n"
    "equations and plotting code. To rebuild after extracting this ZIP, install the listed\n"
    "document dependencies and run: python scripts/build_research_brief.py\n"
    "The regenerated PDF appears in output/pdf; intermediate figures appear in tmp/pdfs.\n"
    "Review all rendered pages after edits. The script checks pagination and formula arithmetic.\n"
    "Document build dependencies: reportlab, matplotlib, numpy, pypdf and Pillow.\n"
    "See build-environment.json for the exact installed versions used.\n\n"
    "These files preserve completed results. No new training or human study was performed\n"
    "while preparing the handoff. Future experiments are labelled as proposals in the brief.\n"
).encode("utf-8")
import importlib.metadata
versions = {package: importlib.metadata.version(package) for package in
            ["reportlab", "matplotlib", "numpy", "pypdf", "Pillow"]}
items["build-environment.json"] = (json.dumps(versions,indent=2)+"\n").encode()
items["SHA256SUMS.json"] = (json.dumps({
    name: hashlib.sha256(payload).hexdigest() for name,payload in sorted(items.items())
}, indent=2)+"\n").encode()
destination = ROOT/"output/pdf/DAWL_research_evidence_bundle.zip"
with zipfile.ZipFile(destination,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for name,payload in sorted(items.items()):
        archive.writestr(name,payload)
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    manifest = json.loads(archive.read("SHA256SUMS.json"))
    assert set(archive.namelist()) == set(manifest)|{"SHA256SUMS.json"}
    for name,expected in manifest.items():
        assert hashlib.sha256(archive.read(name)).hexdigest() == expected
print(json.dumps({"bundle":str(destination),"files":len(items),
                  "bytes":destination.stat().st_size,"versions":versions},indent=2))
