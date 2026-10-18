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

v5.13 personal accounts (both optional; USER_ACCOUNTS unset -> skipped):
      USER_ACCOUNTS  JSON {"Kenton":"<password>",...} - one personal login per engineer
      USER_SECRET    HMAC secret shared with the Worker (>= 32 chars)
  datae.enc  = iv(12) || AES-256-GCM(K, engineer bundle)   K = fresh random 32 bytes EVERY build
  users.json = {"v":1,"iter":150000,"users":{"<lowercase name>": b64(salt16||iv12||AES-GCM(
               PBKDF2(password,salt), {"name","k":b64 K,"tok":hex HMAC-SHA256(USER_SECRET,
               'fufu-user-v1:'+Name)}))}}   (written to the repo root, next to *.enc)
Requires `cryptography`.
"""

import base64, hashlib, hmac, json, os, sys
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pull_notion import applies_to   # same folding rules the app uses (build/ is on sys.path when run as a script)

SRC = "data/bundle.json"
# Worker bundle contents. "svc" is included so field engineers can check a
# machine's past CM history from the item screen — but see worker_svc(): the
# free-text 'Actions Taken' is stripped out, because it can contain credentials
# or WiFi keys R typed in, which the shared-password boundary keeps out of the
# worker's offline file.
WORKER_KEYS = ["asof", "items", "sectors", "pm", "svc", "parts", "siteremarks"]
# Hard guard: admin-only keys (which may now carry credentials in `kb`) must never
# be allowlisted for the worker bundle.
_ADMIN_ONLY = {"kb", "cards", "errors", "procedures", "mref", "mcodes", "kbstatus"}
assert not (set(WORKER_KEYS) & _ADMIN_ONLY), "admin-only key in WORKER_KEYS"

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
    — the worker parts tab is just a lookup list, not analytics. v5.12 adds `fam`:
    the applies-to family NAMES only (no counts), derived at build time."""
    out = [{"name": p.get("name", ""), "pn": p.get("pn", ""),
            "fam": applies_to(p.get("machn"), p.get("n"))} for p in (parts or [])]
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

# Active engineers (mirror of the Worker SUBMITTERS list) - the only names allowed as accounts
ACTIVE_ENGINEERS = ['Anson', 'Poon', 'Pong', 'Alan', 'Yoshida', 'Ivan', 'Ray', 'Joe', 'Eunice', 'Tai', 'Kenton', 'Tim']
ITER = 150000

def parse_accounts(raw, secret, fpw, wpw):
    """Validate USER_ACCOUNTS -> {Canonical: password}. sys.exit(FATAL) on any problem; never prints passwords."""
    try:
        acc = json.loads(raw)
    except ValueError:
        sys.exit("FATAL: USER_ACCOUNTS is not valid JSON.")
    if not isinstance(acc, dict) or not acc:
        sys.exit('FATAL: USER_ACCOUNTS must be a JSON object like {"Kenton":"<password>"}.')
    if len(secret) < 32:
        sys.exit("FATAL: USER_ACCOUNTS is set but USER_SECRET is empty or shorter than 32 characters.")
    canon = {n.lower(): n for n in ACTIVE_ENGINEERS}
    out, seen_pw = {}, set()
    for name, pw in acc.items():
        c = canon.get(str(name).strip().lower())
        if not c:
            sys.exit(f"FATAL: USER_ACCOUNTS name {name!r} is not an active engineer.")
        if c in out:
            sys.exit(f"FATAL: USER_ACCOUNTS has a duplicate name ({c}) after case-folding.")
        if not isinstance(pw, str) or len(pw) < 12:
            sys.exit(f"FATAL: password for {c} must be a string of at least 12 characters.")
        if pw == fpw or (wpw and pw == wpw):
            sys.exit(f"FATAL: password for {c} equals the team FIELD_PW/WORKER_PW - every personal password must be unique.")
        if pw in seen_pw:
            sys.exit(f"FATAL: password for {c} is the same as another account's password.")
        seen_pw.add(pw)
        out[c] = pw
    return out

def write_accounts(accounts, secret, bundle):
    """datae.enc (random key K, engineer bundle) + users.json (K wrapped per user password)."""
    eb = dict(bundle)                      # full admin content (R's decision 2026-10-10)
    eb["role"] = "engineer"
    eb.pop("wkey", None)
    assert "wkey" not in eb, "engineer bundle must never carry the shared write key"
    K = os.urandom(32)
    iv = os.urandom(12)
    ct = AESGCM(K).encrypt(iv, json.dumps(eb, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), None)
    with open("datae.enc", "wb") as f:
        f.write(iv + ct)
    users = {}
    for name, pw in accounts.items():
        tok = hmac.new(secret.encode("utf-8"), ("fufu-user-v1:" + name).encode("utf-8"), hashlib.sha256).hexdigest()
        payload = json.dumps({"name": name, "k": base64.b64encode(K).decode(), "tok": tok},
                             separators=(",", ":")).encode("utf-8")
        salt, uiv = os.urandom(16), os.urandom(12)
        key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITER).derive(pw.encode("utf-8"))
        users[name.lower()] = base64.b64encode(salt + uiv + AESGCM(key).encrypt(uiv, payload, None)).decode()
        print(f"OK: account {name}")
    with open("users.json", "w", encoding="utf-8") as f:
        json.dump({"v": 1, "iter": ITER, "users": users}, f, separators=(",", ":"))
    print(f"OK: datae.enc written ({(len(iv)+len(ct))/1e6:.2f} MB), users.json ({len(users)} accounts)")

def main():
    fpw = os.environ.get("FIELD_PW", "")
    wpw = os.environ.get("WORKER_PW", "")
    wkey = os.environ.get("WRITE_KEY", "")
    if not fpw:
        sys.exit("FATAL: FIELD_PW env var is empty. Set the repo secret.")
    if wpw and wpw == fpw:
        sys.exit("FATAL: WORKER_PW must be different from FIELD_PW — "
                 "the password IS the role.")
    raw = os.environ.get("USER_ACCOUNTS", "").strip()
    # validate personal accounts up front so a bad secret fails the build before anything is written
    accounts = parse_accounts(raw, os.environ.get("USER_SECRET", ""), fpw, wpw) if raw else None
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

    if accounts is not None:
        write_accounts(accounts, os.environ["USER_SECRET"], bundle)
    else:
        print("NOTE: USER_ACCOUNTS not set — datae.enc/users.json skipped (no personal logins).")

if __name__ == "__main__":
    main()
