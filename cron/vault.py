"""
vault.py
========
Encrypts/decrypts the data files so only ciphertext is committed to the
public repo. index.html decrypts status.enc in the browser with the same
scheme (WebCrypto).

Usage:
  python cron/vault.py decrypt   # *.enc -> plaintext (start of CI run)
  python cron/vault.py encrypt   # plaintext -> *.enc (end of CI run)

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

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ITERATIONS = 600_000
DATA_FILES = ["status.json", "history.json", "events.json", "comparison.xlsx"]


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
    """status.json -> status.enc, comparison.xlsx -> comparison.xlsx.enc"""
    return REPO_ROOT / (name.removesuffix(".json") + ".enc")


def credentials() -> tuple[str, str]:
    username = os.environ.get("PAGE_USERNAME", "")
    password = os.environ.get("PAGE_PASSWORD", "")
    if not username or not password:
        sys.exit("vault: PAGE_USERNAME and PAGE_PASSWORD must both be set")
    return username, password


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("encrypt", "decrypt"):
        sys.exit("usage: python cron/vault.py encrypt|decrypt")
    mode = sys.argv[1]
    username, password = credentials()

    for name in DATA_FILES:
        plain = REPO_ROOT / name
        enc = enc_path(name)
        if mode == "encrypt":
            if not plain.exists():
                print(f"vault: skip {name} (missing)")
                continue
            enc.write_text(json.dumps(encrypt(plain.read_bytes(), username, password)))
            print(f"vault: {name} -> {enc.name}")
        else:
            if not enc.exists():
                print(f"vault: skip {enc.name} (missing)")
                continue
            plain.write_bytes(decrypt(json.loads(enc.read_text()), username, password))
            print(f"vault: {enc.name} -> {name}")


if __name__ == "__main__":
    main()
