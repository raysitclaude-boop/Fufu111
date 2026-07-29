#!/usr/bin/env python3
"""
encrypt_bundle.py — encrypt data/bundle.json for the FUJI Field PWA (V5).

Produces TWO encrypted bundles:
  data.enc   = FULL bundle,   password FIELD_PW   (engineers / admin)
  dataw.enc  = WORKER bundle, password WORKER_PW  (junior staff — Sites/Serial/PM
               only; svc, parts, cards, errors, procedures are NOT in the file,
               so the restriction is cryptographic, not just hidden UI)

V5 also embeds WRITE_KEY into both bundles as bundle["wkey"], so nobody types
the write key anymore — logging in IS the write authorization (it never leaves
the encrypted file). bundle["role"] tells the app which UI to show.

Format MUST match index.html unlock():
    *.enc = salt(16) || iv(12) || AES-256-GCM ciphertext
    key   = PBKDF2-HMAC-SHA256(password, salt, 150000 iterations)

env:  FIELD_PW   shared team password (repo secret, required)
      WORKER_PW  worker-account password (repo secret; if unset, dataw.enc is
                 skipped and worker login simply doesn't exist)
      WRITE_KEY  the Worker proxy write key (repo secret; if unset, the app
                 falls back to manual key entry like V4)
Requires `cryptography`.
"""

import json, os, sys
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SRC = "data/bundle.json"
# Worker bundle contents. "svc" is included so field engineers can check a
# machine's past CM history from the item screen — but see worker_svc(): the
# free-text 'Actions Taken' is stripped out, because it can contain credentials
# or WiFi keys R typed in, which the shared-password boundary keeps out of the
# worker's offline file.
WORKER_KEYS = ["asof", "items", "sectors", "pm", "svc", "parts"]

def worker_svc(svc):
    """Structured CM fields only for the worker bundle: date, machine, serial,
    problem types, error codes, engineer, symptoms, parts, status — but NOT the
    free-text Actions Taken ('act')."""
    keep = ("d", "site", "mach", "sn", "pt", "err", "pic", "sym", "parts", "st", "l2", "rv")
    out = []
    for s in svc or []:
        out.append({k: s.get(k) for k in keep if k in s})
    return out

def worker_parts(parts):
    """Plain parts reference for the worker: name + part number only. Drops the
    usage frequency ('n'), the Problem-L2 breakdown ('l2') and machines ('mach')
    — the worker parts tab is just a lookup list, not analytics."""
    out = [{"name": p.get("name", ""), "pn": p.get("pn", "")} for p in (parts or [])]
    out.sort(key=lambda p: p["name"].lower())
    return out

def encrypt(dst, pw, obj):
    plaintext = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    salt = os.urandom(16)
    iv = os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=150000).derive(pw.encode("utf-8"))
    ct = AESGCM(key).encrypt(iv, plaintext, None)
    with open(dst, "wb") as f:
        f.write(salt + iv + ct)
    print(f"OK: {dst} written ({(len(salt)+len(iv)+len(ct))/1e6:.2f} MB).")

def main():
    fpw = os.environ.get("FIELD_PW", "")
    wpw = os.environ.get("WORKER_PW", "")
    wkey = os.environ.get("WRITE_KEY", "")
    if not fpw:
        sys.exit("FATAL: FIELD_PW env var is empty. Set the repo secret.")
    if wpw and wpw == fpw:
        sys.exit("FATAL: WORKER_PW must be different from FIELD_PW — "
                 "the password IS the role.")
    if not os.path.exists(SRC):
        sys.exit(f"FATAL: {SRC} not found — run build/pull_notion.py first.")
    with open(SRC, encoding="utf-8") as f:
        bundle = json.load(f)

    full = dict(bundle)
    full["role"] = "admin"
    if wkey:
        full["wkey"] = wkey
    else:
        print("NOTE: WRITE_KEY not set — app will ask for the key manually (V4 behaviour).")
    encrypt("data.enc", fpw, full)

    if wpw:
        wb = {k: bundle.get(k) for k in WORKER_KEYS if k in bundle}
        if "svc" in wb:
            wb["svc"] = worker_svc(wb["svc"])       # drop free-text before it leaves the build
        if "parts" in wb:
            wb["parts"] = worker_parts(wb["parts"]) # name + P/N only, no frequency
        wb["role"] = "worker"
        if wkey:
            wb["wkey"] = wkey
        encrypt("dataw.enc", wpw, wb)
    else:
        print("NOTE: WORKER_PW not set — dataw.enc skipped (no worker login).")

if __name__ == "__main__":
    main()
