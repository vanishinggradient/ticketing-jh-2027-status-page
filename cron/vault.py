"""
vault.py
========
Encrypts/decrypts the data files so only ciphertext is committed to the
public repo. index.html decrypts status.enc in the browser with the same
scheme (WebCrypto).

Usage:
  python cron/vault.py decrypt [file ...]   # *.enc -> plaintext (start of CI run)
  python cron/vault.py encrypt [file ...]   # plaintext -> *.enc (end of CI run)

Files default to all of DATA_FILES. encrypt leaves an .enc untouched when its
plaintext hasn't changed, so CI and the local events job (cron/local_events.sh)
never rewrite each other's files and their pushes don't conflict.

Scheme: AES-256-GCM, key = PBKDF2-HMAC-SHA256(username:password, salt, 600k).
600k iterations is the OWASP Password Storage Cheat Sheet minimum.

Env vars:
  PAGE_USERNAME : Login username (case-insensitive, trimmed)
  PAGE_PASSWORD : Login password
"""

import base64
import hashlib
import json
import os
import pathlib
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ITERATIONS = 600_000
DATA_FILES = ["status.json", "history.json", "events.json"]


def derive_key(username: str, password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    secret = f"{username.strip().lower()}:{password}".encode()
    return hashlib.pbkdf2_hmac("sha256", secret, salt, iterations, dklen=32)


def encrypt(data: bytes, username: str, password: str) -> dict:
    salt = os.urandom(16)
    iv = os.urandom(12)
    ct = AESGCM(derive_key(username, password, salt)).encrypt(iv, data, None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt(envelope: dict, username: str, password: str) -> bytes:
    """Raises cryptography.exceptions.InvalidTag on wrong credentials."""
    salt = base64.b64decode(envelope["salt"])
    iv = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ct"])
    key = derive_key(username, password, salt, envelope["iter"])
    return AESGCM(key).decrypt(iv, ct, None)


def enc_path(name: str) -> pathlib.Path:
    """status.json -> status.enc"""
    return REPO_ROOT / (name.removesuffix(".json") + ".enc")


def credentials() -> tuple[str, str]:
    username = os.environ.get("PAGE_USERNAME", "")
    password = os.environ.get("PAGE_PASSWORD", "")
    if not username or not password:
        sys.exit("vault: PAGE_USERNAME and PAGE_PASSWORD must both be set")
    return username, password


def encrypt_files(names: list[str], username: str, password: str):
    for name in names:
        plain = REPO_ROOT / name
        enc = enc_path(name)
        if not plain.exists():
            print(f"vault: skip {name} (missing)")
            continue
        data = plain.read_bytes()
        if enc.exists():
            try:
                if decrypt(json.loads(enc.read_text()), username, password) == data:
                    print(f"vault: {enc.name} unchanged")
                    continue
            except InvalidTag:
                pass  # different credentials; overwrite
        enc.write_text(json.dumps(encrypt(data, username, password)))
        print(f"vault: {name} -> {enc.name}")


def decrypt_files(names: list[str], username: str, password: str):
    for name in names:
        enc = enc_path(name)
        if not enc.exists():
            print(f"vault: skip {enc.name} (missing)")
            continue
        (REPO_ROOT / name).write_bytes(decrypt(json.loads(enc.read_text()), username, password))
        print(f"vault: {enc.name} -> {name}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("encrypt", "decrypt"):
        sys.exit("usage: python cron/vault.py encrypt|decrypt [file ...]")
    names = sys.argv[2:] or DATA_FILES
    unknown = [n for n in names if n not in DATA_FILES]
    if unknown:
        sys.exit(f"vault: unknown file(s) {unknown}; expected some of {DATA_FILES}")
    username, password = credentials()
    (encrypt_files if sys.argv[1] == "encrypt" else decrypt_files)(names, username, password)


if __name__ == "__main__":
    main()
