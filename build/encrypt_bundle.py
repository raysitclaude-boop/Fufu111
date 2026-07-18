#!/usr/bin/env python3
"""
encrypt_bundle.py — encrypt data/bundle.json -> data.enc for the FUJI Field PWA.

Format MUST match index.html unlock():
    data.enc = salt(16) || iv(12) || AES-256-GCM ciphertext
    key      = PBKDF2-HMAC-SHA256(password, salt, 150000 iterations)

env FIELD_PW = shared team password (repo secret). Requires `cryptography`.
"""

import os, sys
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SRC, DST = "data/bundle.json", "data.enc"

def main():
    pw = os.environ.get("FIELD_PW", "")
    if not pw:
        sys.exit("FATAL: FIELD_PW env var is empty. Set the repo secret.")
    if not os.path.exists(SRC):
        sys.exit(f"FATAL: {SRC} not found — run build/pull_notion.py first.")
    with open(SRC, "rb") as f:
        plaintext = f.read()

    salt = os.urandom(16)
    iv = os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=150000).derive(pw.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, plaintext, None)

    with open(DST, "wb") as f:
        f.write(salt + iv + ct)
    print(f"OK: {DST} written ({(len(salt)+len(iv)+len(ct))/1e6:.2f} MB).")

if __name__ == "__main__":
    main()
