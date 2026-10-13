"""Bounded serialization, immutable file publication, and Ed25519 identities."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import secrets
import shutil
import struct
import tempfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from data_ingestion.protocol import canonical_json, sha256, require_digest

MAX_JSON = 32 * 1024 * 1024
SIGNING_DOMAIN = b"DAWL/ingestion-node/v2\x00"


@contextmanager
def staging_directory(parent: Path, prefix: str):
    """Stage publishable data with inherited Windows ACLs and POSIX mode 0750.

    TemporaryDirectory uses mode 0700, which recent Windows Python translates
    into OWNER RIGHTS ACLs that WSL bind mounts cannot read as an ordinary UID.
    Identities never use this directory; callers control access to the store.
    """
    parent = parent.resolve()
    directory = parent / (prefix + secrets.token_hex(16))
    directory.mkdir(mode=0o750)
    try:
        yield directory
    finally:
        if directory.exists():
            if directory.is_symlink() or directory.resolve().parent != parent:
                raise ValueError("staging cleanup target escaped its parent")
            shutil.rmtree(directory)


def parse_json(payload: bytes, limit: int = MAX_JSON):
    if len(payload) > limit:
        raise ValueError("JSON exceeds the configured byte limit")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result
    try:
        return json.loads(payload, object_pairs_hook=unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, RecursionError, TypeError) as error:
        raise ValueError("invalid JSON") from error


def read_bytes(path: str | Path, limit: int = MAX_JSON) -> bytes:
    with Path(path).open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"file exceeds {limit} bytes: {Path(path).name}")
    return payload


def read_json(path: str | Path):
    return parse_json(read_bytes(path))


def immutable_write(path: Path, payload: bytes) -> None:
    """Atomic publication by hard link; an existing differing value is an error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_bytes(path, max(MAX_JSON, len(payload))) != payload:
                raise ValueError(f"immutable artifact conflicts: {path.name}")
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path: Path, value) -> None:
    immutable_write(path, canonical_json(value))


def pack_ids(ids: list[int] | tuple[int, ...]) -> bytes:
    if any(type(token) is not int or not 0 <= token <= 0xFFFFFFFF for token in ids):
        raise ValueError("invalid uint32 token ID")
    return struct.pack(f"<{len(ids)}I", *ids)


def unpack_ids(payload: bytes) -> list[int]:
    if len(payload) % 4:
        raise ValueError("truncated token file")
    return [item[0] for item in struct.iter_unpack("<I", payload)]


def keygen(private_path: Path, public_path: Path) -> str:
    if private_path.exists() or public_path.exists():
        raise ValueError("identity files already exist; refusing to replace them")
    key = Ed25519PrivateKey.generate()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    payload = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    descriptor = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    public = key.public_key().public_bytes_raw().hex()
    immutable_write(public_path, (public + "\n").encode("ascii"))
    return public


def load_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(read_bytes(path, 8192), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("an Ed25519 key is required")
    return key


def load_public(path: Path) -> str:
    public = read_bytes(path, 1024).decode("ascii").strip()
    require_digest(public)  # Ed25519 public keys are also 32 bytes, represented as lowercase hex.
    return public


def sign(body: dict, key: Ed25519PrivateKey) -> dict:
    body = {**body, "signer": key.public_key().public_bytes_raw().hex()}
    payload = canonical_json(body)
    return {"id": sha256(payload), "body": body,
            "signature": key.sign(SIGNING_DOMAIN + payload).hex()}


def verify_signature(envelope: dict, expected_public: str) -> dict:
    try:
        if set(envelope) != {"id", "body", "signature"}:
            raise ValueError("invalid signed envelope fields")
        require_digest(expected_public)
        body = envelope["body"]
        if body["signer"] != expected_public:
            raise ValueError("untrusted signing identity")
        payload = canonical_json(body)
        if sha256(payload) != envelope["id"]:
            raise ValueError("signed content identifier mismatch")
        signature = bytes.fromhex(envelope["signature"])
        if len(signature) != 64:
            raise ValueError("invalid signature length")
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(expected_public)).verify(
            signature, SIGNING_DOMAIN + payload)
        return body
    except (InvalidSignature, KeyError, TypeError, AttributeError) as error:
        raise ValueError("signature verification failed") from error
