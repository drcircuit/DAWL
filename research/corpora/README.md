# Classics 8, version 1

A small, recognizable collection for testing ingestion across multiple books,
document parts, token shards and audit requests. The lock was developed in the
research worktree and is reused unchanged by the DAWL platform. No model training
or retrieval-quality evaluation is implied. Selection and rights review date: 2026-10-13.

The [platform validation report](../../docs/platform_run_001.md) records the current
token, vector and provenance replay. The previous corpus-only validation remains
in the separate research branch.

| Gutenberg ID | Selected work | Author (death year) |
| --- | --- | --- |
| [11](https://www.gutenberg.org/ebooks/11) | Alice's Adventures in Wonderland | Lewis Carroll (1898) |
| [1342](https://www.gutenberg.org/ebooks/1342) | Pride and Prejudice | Jane Austen (1817) |
| [84](https://www.gutenberg.org/ebooks/84) | Frankenstein | Mary Wollstonecraft Shelley (1851) |
| [2701](https://www.gutenberg.org/ebooks/2701) | Moby Dick; Or, The Whale | Herman Melville (1891) |
| [1400](https://www.gutenberg.org/ebooks/1400) | Great Expectations | Charles Dickens (1870) |
| [76](https://www.gutenberg.org/ebooks/76) | Adventures of Huckleberry Finn | Mark Twain (1910) |
| [345](https://www.gutenberg.org/ebooks/345) | Dracula | Bram Stoker (1912) |
| [1661](https://www.gutenberg.org/ebooks/1661) | The Adventures of Sherlock Holmes | Arthur Conan Doyle (1930) |

## Source and rights basis

The linked catalogues and downloaded RDF records declare these editions public
domain in the USA. The downloaded headers use the standard unrestricted-work
notice, with the full Project Gutenberg licence retained. We use original English
texts, not modern translations. The 1342 edition also includes a preface by
[George Saintsbury (died 1933)](https://www.gutenberg.org/ebooks/31698).

For local use in Norway, the underlying authors' lifetimes and historical
publication supply the additional term-expiry basis: Norway's government
describes the general EEA term as life plus 70 years
([official explanation](https://www.regjeringen.no/no/tema/kultur-idrett-og-frivillighet/opphavsrett/innsiktsartikler/Opphavsrett1/id2000944/)).
That is a rights assessment for these selected historical texts, not a claim
that every Gutenberg download or future added edition is unrestricted everywhere.
Original metadata and rights declarations are retained for review.

The electronic editions remain subject to the
[Project Gutenberg licence](https://www.gutenberg.org/policy/license.html),
including its trademark and redistribution terms. This fixture retains the
supplied notices and credits; it does not relabel the full ebook files as CC0.
Full original texts and metadata stay in the ignored local cache. They are not
published as a new branded anthology. Cryptographic checks establish byte
consistency, not copyright ownership or the truth of a catalogue declaration.

## Acquisition and versioning

The downloader uses the project's documented
[rsync mirror service](https://www.gutenberg.org/help/mirroring.html), selecting
only eight text files and their eight RDF records. It does not crawl the website.
The [automation guidance](https://www.gutenberg.org/policy/robot_access.html)
notes that ebooks and generated files change; a title or URL alone is therefore
insufficient for reproducible experiments.

`classics-8-v1.json` specifies the selection. `classics-8-v1.lock.json` records
the exact text and RDF SHA-256 hashes, byte counts, source/download URLs, retrieval
time, and catalogue declarations. First acquisition pins those bytes. Existing
files are rechecked against that lock; changed selections or bytes fail rather
than silently refreshing the corpus. Mirror acquisition itself is not a
cryptographic assertion of authorship or a trusted publication signature.

Raw files remain at `artifacts/book-corpus/raw/<id>/pg<id>.txt` and `.rdf`.
Keep that cache with the research archive: if an upstream edition changes, the
lock correctly rejects it and the original local bytes are needed for exact
reproduction. Generating fresh keys changes snapshot IDs but not the book hashes.

## Exact-byte processing and limits

The collection contains **5,850,515 original UTF-8 bytes**. The ingestion node's
maximum document size is 1 MiB. Deterministic parts of at most 512 KiB allow the
larger books to enter that bounded contract without changing their source bytes.

Part boundaries never split a UTF-8 character. `book-parts.json` records original
book hashes, byte starts and exclusive ends, part hashes and document IDs.
Reassembly must equal the downloaded bytes before ingestion. Source URI fragments
bind each original book hash and byte range into registered document metadata.
The tokenizer then uses the existing 4 KiB chunk contract and 65,536-token shard
target. Byte parts and token chunks serve different size limits; neither is a
semantic chapter or a model context window.

There is no whitespace normalization, newline conversion, Unicode normalization,
boilerplate removal, translation or deduplication beyond the inherited exact
document-duplicate mechanism. The texts include publisher/front-matter and
licence boilerplate. This is an ingestion fixture, not a cleaned training dataset.
Any cleaning or later train/validation/test partition needs a new explicit
transformation and corpus version. A handful of historical English books does
not establish multilingual, web-scale or modern-domain performance.

## Local commands

From the platform checkout:

```powershell
python scripts/book_corpus.py fetch
python dawl_platform.py prepare
python dawl_platform.py tokenize --workers 4
```

On Windows the fetch step uses rsync in `kali-linux` by default; select another
installed distro with `--wsl-distro`. On Linux it calls rsync directly. It requests
only missing files; existing mismatched bytes are retained and rejected for
inspection. Preparation requires a new output directory, preserving previous runs.
See the [platform runbook](../../docs/platform.md) for pinned model assets, vector
reconstruction, CPU container audit, public verification and benchmark commands.
