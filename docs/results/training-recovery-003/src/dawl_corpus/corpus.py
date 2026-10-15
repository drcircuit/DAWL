"""Immutable corpus locks; UTF-8 source bytes survive registration unchanged."""
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
import subprocess
import sys
from time import perf_counter
import xml.etree.ElementTree as ET

from dawl_node.common import canonical_json, immutable_write, keygen, read_bytes, read_json, sha256, write_json
from dawl_node.source import register
from dawl_node.store import mine
from dawl_node.tokenizer import PinnedTokenizer

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
PG = "http://www.gutenberg.org/2009/pgterms/"
DC = "http://purl.org/dc/terms/"
MIRROR = "rsync://gutenberg.pglaf.org/gutenberg-epub/"


def validate_book(book, text_bytes, rdf_bytes):
    text = text_bytes.decode("utf-8", errors="strict")
    header, marker, _ = text.partition("*** START OF THE PROJECT GUTENBERG EBOOK")
    if not marker or not re.search(r"\[eBook\s+#" + str(book["id"]) + r"\]", header, re.I):
        raise ValueError("unexpected book identity or missing Gutenberg start marker")
    if not re.search(r"THE FULL PROJECT GUTENBERG(?:™|-tm)? LICENSE", text):
        raise ValueError("required original license notice missing")
    if re.search(r"copyrighted|used by permission|with permission of|copyright\s+\d{4}", header, re.I):
        raise ValueError("header requires a separate rights review")
    root = ET.fromstring(rdf_bytes)
    ebook = root.find("{" + PG + "}ebook")
    if ebook is None or not ebook.get("{" + RDF + "}about", "").endswith("/" + str(book["id"])):
        raise ValueError("catalogue identity mismatch")
    rights = ebook.findtext("{" + DC + "}rights")
    if rights != "Public domain in the USA.":
        raise ValueError("corpus requires the selected public-domain catalogue status")
    language = ebook.find("{" + DC + "}language/{" + RDF + "}Description/{" + RDF + "}value")
    if language is None or language.text != "en":
        raise ValueError("expected English text")
    return {"catalogue_title": ebook.findtext("{" + DC + "}title"), "rights_declaration": rights,
            "catalogue_url": f"https://www.gutenberg.org/ebooks/{book['id']}",
            "header_check": "expected ID, standard notices, no restricted-use header flag"}


def fetch(selection_path, raw, lock_path, distro="kali-linux"):
    selection = read_json(selection_path)
    books = selection["books"]
    ids = [b["id"] for b in books]
    if (selection["schema"] != "dawl.book-selection/1" or selection["download_root"] != MIRROR or
        not 1 <= len(books) <= 32 or len(set(ids)) != len(ids) or
        any(type(i) is not int or not 1 <= i <= 1000000 for i in ids)):
        raise ValueError("invalid bounded corpus selection")
    selection_hash = sha256(canonical_json(selection))
    existing = read_json(lock_path) if lock_path.exists() else None
    if existing and existing["selection_sha256"] != selection_hash:
        raise ValueError("selection differs from the frozen lock; create a new corpus version")
    raw.mkdir(parents=True, exist_ok=True)
    missing = [f"{i}/pg{i}.{suffix}" for i in ids for suffix in ("txt", "rdf") if not (raw / str(i) / f"pg{i}.{suffix}").exists()]
    if missing:
        paths = raw / "requested-files.txt"
        paths.write_text("\n".join(missing) + "\n", encoding="ascii", newline="\n")
        if sys.platform == "win32":
            def linux_path(p):
                return subprocess.check_output(["wsl", "-d", distro, "--exec", "wslpath", "-a", str(p.resolve())], text=True).strip()
            command = ["wsl", "-d", distro, "--exec", "rsync", "-r", "--contimeout=10", "--timeout=30",
                       "--max-size=8m", "--files-from=" + linux_path(paths), MIRROR, linux_path(raw) + "/"]
        else:
            command = ["rsync", "-r", "--contimeout=10", "--timeout=30", "--max-size=8m",
                       "--files-from=" + str(paths), MIRROR, str(raw) + "/"]
        subprocess.run(command, check=True)
    entries = []
    for book in books:
        base = raw / str(book["id"])
        text_bytes = read_bytes(base / f"pg{book['id']}.txt", 8 * 1024 * 1024)
        rdf_bytes = read_bytes(base / f"pg{book['id']}.rdf", 1024 * 1024)
        info = validate_book(book, text_bytes, rdf_bytes)
        entries.append({**book, **info, "source_url": f"https://www.gutenberg.org/cache/epub/{book['id']}/pg{book['id']}.txt",
                        "download_uri": MIRROR + f"{book['id']}/pg{book['id']}.txt",
                        "sha256": sha256(text_bytes), "bytes": len(text_bytes), "metadata_sha256": sha256(rdf_bytes)})
    if existing:
        if existing["books"] != entries:
            raise ValueError("cached or downloaded bytes differ from corpus lock; no automatic updates")
        return existing
    lock = {"schema": "dawl.book-corpus-lock/1", "corpus": selection["corpus"], "selection_sha256": selection_hash,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "processing": selection["processing"],
            "books": entries, "source_bytes": sum(e["bytes"] for e in entries)}
    write_json(lock_path, lock)
    return lock


def partition_book(book, payload, part_bytes=512 * 1024):
    if type(part_bytes) is not int or not 4 <= part_bytes <= 1024 * 1024:
        raise ValueError("book part size must be 4..1048576 bytes")
    payload.decode("utf-8", errors="strict")
    if not payload or sha256(payload) != book["sha256"]:
        raise ValueError("book partition input differs from its lock")
    start, index = 0, 0
    while start < len(payload):
        end = min(start + part_bytes, len(payload))
        while end < len(payload) and payload[end] & 0xC0 == 0x80:
            end -= 1
        part = payload[start:end]
        identity = f"gutenberg:{book['id']}:part:{index:04d}"
        document = {"id": identity, "text": part.decode("utf-8"),
                    "source_uri": book["source_url"] + f"#book-sha256={book['sha256']}&bytes={start}-{end}",
                    "license": "Project Gutenberg License retained in complete source; catalogue declares public domain in the USA; see corpus rights review"}
        mapping = {"document_id": identity, "book_id": book["id"], "book_sha256": book["sha256"],
                   "part": index, "byte_start": start, "byte_end_exclusive": end, "sha256": sha256(part)}
        yield document, mapping
        start, index = end, index + 1


def prepare(lock_path, raw, tokenizer_path, tokenizer_sha256, output):
    lock = read_json(lock_path)
    documents, parts = [], []
    for book in lock["books"]:
        payload = read_bytes(raw / str(book["id"]) / f"pg{book['id']}.txt", 8 * 1024 * 1024)
        if len(payload) != book["bytes"] or sha256(payload) != book["sha256"]:
            raise ValueError("book differs from corpus lock")
        divided = list(partition_book(book, payload))
        if b"".join(doc["text"].encode("utf-8") for doc, _ in divided) != payload:
            raise ValueError("book parts do not reassemble exactly")
        documents.extend(doc for doc, _ in divided)
        parts.extend(part for _, part in divided)
    tokenizer = PinnedTokenizer(tokenizer_path, tokenizer_sha256)
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source.jsonl"
    immutable_write(source, b"\n".join(canonical_json(d) for d in documents))
    part_map = {"schema": "dawl.book-parts/1", "max_part_bytes": 512 * 1024, "parts": parts}
    write_json(output / "book-parts.json", part_map)
    identity = output / "miner-identity"
    keygen(identity / "key.private.pem", identity / "public.key")
    immutable_write(identity / "api.token", secrets.token_urlsafe(32).encode())
    start = perf_counter()
    register(source, output / "trust/source.json", 4096)
    registration_seconds = perf_counter() - start
    immutable_write(output / "trust/tokenizer.json", tokenizer.payload)
    immutable_write(output / "trust/miner-public.key", (identity / "public.key").read_bytes())
    start = perf_counter()
    run = mine(source, output / "trust/source.json", tokenizer, identity / "key.private.pem", output / "miner", shard_tokens=65536)
    run.update(tokenizer=tokenizer.descriptor, corpus=lock["corpus"], corpus_lock_sha256=sha256(lock_path.read_bytes()),
               source_bytes=lock["source_bytes"], book_count=len(lock["books"]), source_documents=len(documents),
               parts_sha256=sha256(canonical_json(part_map)), registration_seconds=registration_seconds,
               preparation_seconds=perf_counter() - start, prepared_at_utc=datetime.now(timezone.utc).isoformat())
    write_json(output / "run.json", run)
    return run
