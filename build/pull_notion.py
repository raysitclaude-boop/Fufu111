#!/usr/bin/env python3
"""
pull_notion.py — nightly data pull for the FUJI Field PWA (V3 read-only bundle).

Rebuilt 2026-07-18 (original was never committed to the repo — this restores the
nightly refresh). Runs inside GitHub Actions:

    env NOTION_TOKEN  = Notion internal-integration token (repo secret)
    output            = data/bundle.json   (raw, NEVER committed — encrypt_bundle.py
                        turns it into data.enc, workflow deletes data/*.json)

Design rules (KB_PWA_Architecture_Spec.md §2.3, binding):
  * ALLOWLIST: only the data sources / pages enumerated below are pulled.
  * Token never written anywhere.
  * Leak check on output: hard-fail on high-confidence credential markers,
    loud warning on soft markers (so a stray word can't brick the nightly build).

Bundle schema (must match index.html):
  asof, items[], sectors{}, svc[], pm[], parts[], cards[], errors[], procedures{}

Stdlib only (urllib) except nothing — `cryptography` is only needed by encrypt_bundle.py.
"""

import json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

# ----------------------------------------------------------------------------
# ALLOWLIST — the ONLY Notion objects this script reads
# ----------------------------------------------------------------------------
DB_ITEM_MASTER = "c508cead-7e1b-4990-8c30-a95623287c12"   # Fujifilm Item Master List
DB_CM          = "2f6a9088-5bbb-803f-8956-c0715dcd20de"   # CM_Service_Record_DataBase
DB_PM          = "35aa9088-5bbb-80ff-9385-f3b29436cdd6"   # PM Master List

# Solution Cards (id slug MUST match CARD_L2 keys in index.html)
CARDS = [
    ("artifacts",         "Detector Artifacts",                          "Detector", ["D-evo", "Nano", "Go Plus"],          "artifact white dots vertical lines grid tomo", "39ba9088-5bbb-81af-9966-cda31bc97ea1"),
    ("charging-pins",     "Detector Charging Pins / Cannot Charge",      "Detector", ["Nano", "D-evo"],                     "charging pins cannot charge power on DSC65A",  "39ba9088-5bbb-813c-bc69-ebc2ee2a95b4"),
    ("bcr",               "Barcode Reader (BCR) Malfunction",            "Hardware", ["Go Plus", "Nano"],                   "barcode reader BCR scan disconnect",           "39ba9088-5bbb-81fc-bdcf-fd35115bd214"),
    ("cable",             "Cable / Connector Faults",                    "Hardware", ["Go Plus", "Nano", "D-evo"],          "cable connector DAP SE MP power",              "39ba9088-5bbb-8155-b11d-ff39bff53263"),
    ("handswitch",        "Hand Switch Malfunction / Cannot Shoot",      "Hardware", ["Go Plus"],                           "hand switch wireless cannot shoot exposure",   "39ba9088-5bbb-8119-b863-f32cbcf23216"),
    ("charging-plug",     "Go Plus Charging Plug / Power Socket",        "Hardware", ["Go Plus"],                           "charging plug power socket F45 F56",           "39ba9088-5bbb-8143-9f23-f6865f3cc60a"),
    ("collimator-handle", "Collimator Handle Broken / Loose",            "Hardware", ["Go Plus"],                           "collimator handle broken loose",               "39ba9088-5bbb-81ff-abbe-f0e1540dd234"),
    ("bumper",            "Go Plus Bumper / Error A9",                   "Hardware", ["Go Plus"],                           "bumper A9 collision sensor",                   "39ba9088-5bbb-8178-9867-df8aa4ff3bd0"),
    ("wifi",              "Detector Wifi Disconnection",                 "Network",  ["Go Plus", "Nano", "D-evo"],          "wifi disconnect wireless signal AP",           "39ba9088-5bbb-811b-b71a-d55cba84516b"),
    ("wl-pacs",           "WL / PACS: Image Cannot Send, Worklist",      "Network",  ["Console", "All"],                    "worklist PACS send image DICOM IP",            "39ba9088-5bbb-81a6-9198-fe9ec02c3786"),
    ("uu-settings",       "Console UU Settings",                        "Software", ["Console"],                           "UU settings protocol marker print display",    "39ba9088-5bbb-8145-9179-e35df69da2e2"),
]

# Error-code index pages (hand-copied manual tables in Notion)
ERROR_PAGES = [
    ("goplus", "FDR Go Plus — Error Codes & Parts", "Hand-copied from official manual pages in Notion — verify against the unit.", "4457ef81-90f9-49cb-b950-042d666950e2"),
    ("nano",   "FDR Nano (DR-XD1000) — Error Codes", "Hand-copied from official manual pages in Notion — verify against the unit.", "d8f3787a-8585-43da-af4a-5569e6f4bd29"),
    ("devo3",  "D-evo 3 (DR-ID 1800) — Error Codes", "Hand-copied from official manual pages in Notion — verify against the unit.", "33ca9088-5bbb-807b-81ba-fc9f84744079"),
]

# Procedure docs (rendered as collapsible md docs in Fix > Procedures)
PROCEDURE_DOCS = [
    # (tag shown in UI, page id)
    ("QA", "2f3a9088-5bbb-808a-bdb0-f2ef113ae661"),   # Calibration / QA values page
]

# Leak check
HARD_MARKERS = ["306a9088-5bbb-807d-8d54-ee8f0955e40f",  # Hospital Specific Info page id
                "wifi-pw", "wifi pw", "console login"]
SOFT_MARKERS = ["password", "passwd", "登入"]

API = "https://api.notion.com/v1"
TOKEN = os.environ.get("NOTION_TOKEN", "").strip()
OUT_DIR = "data"

# ----------------------------------------------------------------------------
# Notion API helpers (stdlib urllib, retry w/ backoff, 429-aware)
# ----------------------------------------------------------------------------
def api(path, payload=None, method=None):
    url = API + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
        method=method or ("POST" if data else "GET"),
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            if e.code == 429 or e.code >= 500:
                wait = int(e.headers.get("Retry-After", 2)) + attempt * 2
                print(f"  retry {e.code} in {wait}s: {path}")
                time.sleep(wait); continue
            if e.code == 401:
                sys.exit(f"FATAL 401: NOTION_TOKEN invalid/expired. Rotate the repo secret. ({path})")
            if e.code == 404:
                sys.exit(f"FATAL 404: object not shared with the integration or moved: {path}\n{body}\n"
                         "-> In Notion open the page > ... > Connections > add the integration.")
            sys.exit(f"FATAL {e.code} on {path}: {body}")
        except Exception as e:
            print(f"  network error ({e}), retry"); time.sleep(3 + attempt * 3)
    sys.exit(f"FATAL: giving up on {path}")

def query_db(dbid):
    rows, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor: payload["start_cursor"] = cursor
        res = api(f"/databases/{dbid}/query", payload)
        rows += res.get("results", [])
        if not res.get("has_more"): break
        cursor = res.get("next_cursor")
    return rows

def block_children(bid):
    out, cursor = [], None
    while True:
        q = f"/blocks/{bid}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
        res = api(q)
        out += res.get("results", [])
        if not res.get("has_more"): break
        cursor = res.get("next_cursor")
    return out

# ----------------------------------------------------------------------------
# Property extraction (type-agnostic — survives select<->text schema drift)
# ----------------------------------------------------------------------------
def rich(rt): return "".join(t.get("plain_text", "") for t in (rt or []))

def pval(p):
    """Return str for scalar-ish props, list[str] for multi-ish, '' if empty."""
    if not p: return ""
    t = p.get("type")
    v = p.get(t)
    if v is None: return ""
    if t in ("title", "rich_text"): return rich(v).strip()
    if t == "select": return v.get("name", "")
    if t == "status": return v.get("name", "")
    if t == "multi_select": return [o.get("name", "") for o in v]
    if t == "date": return (v.get("start") or "")[:10]
    if t == "number": return v
    if t == "checkbox": return v
    if t == "formula":
        ft = v.get("type"); return v.get(ft) if ft in ("string", "number", "boolean") else ""
    if t == "people": return [u.get("name", "") for u in v]
    return ""

def norm(name):  # property names may contain embedded newlines ("PM \nno")
    return re.sub(r"\s+", " ", name or "").strip().lower()

def prop(props, name):
    n = norm(name)
    for k, v in props.items():
        if norm(k) == n: return pval(v)
    return ""

def aslist(v):
    if isinstance(v, list): return [x for x in v if x]
    return [v] if v else []

# ----------------------------------------------------------------------------
# Blocks -> markdown (subset matching the app's md() renderer)
# ----------------------------------------------------------------------------
def render_blocks(blocks, depth=0):
    out = []
    for b in blocks:
        t = b.get("type"); d = b.get(t, {})
        txt = rich(d.get("rich_text"))
        if t == "heading_1": out.append("## " + txt)
        elif t == "heading_2": out.append("## " + txt)
        elif t == "heading_3": out.append("### " + txt)
        elif t == "paragraph":
            if txt: out.append(txt)
        elif t in ("bulleted_list_item", "toggle"):
            out.append("- " + txt)
        elif t == "numbered_list_item":
            out.append("1. " + txt)
        elif t == "to_do":
            out.append("- " + ("[x] " if d.get("checked") else "[ ] ") + txt)
        elif t == "callout":
            if txt: out.append("**" + txt + "**")
        elif t == "quote":
            if txt: out.append(txt)
        elif t == "code":
            out.append("`" + rich(d.get("rich_text")) + "`")
        elif t == "divider":
            out.append("---")
        elif t == "table":
            out.append(render_table(b))
            continue  # table children already consumed
        # recurse into children (nested lists, toggle bodies) — skip child pages/DBs
        if b.get("has_children") and t not in ("child_page", "child_database", "table"):
            if depth < 3:
                out.append(render_blocks(block_children(b["id"]), depth + 1))
    return "\n".join(x for x in out if x)

def table_rows(table_block):
    rows = []
    for r in block_children(table_block["id"]):
        if r.get("type") == "table_row":
            rows.append([rich(c).strip() for c in r["table_row"].get("cells", [])])
    return rows

def render_table(table_block):
    rows = table_rows(table_block)
    if not rows: return ""
    md = ["| " + " | ".join(c.replace("|", "/") for c in rows[0]) + " |",
          "|" + "---|" * len(rows[0])]
    for r in rows[1:]:
        md.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in r) + " |")
    return "\n".join(md)

# ----------------------------------------------------------------------------
# Section builders
# ----------------------------------------------------------------------------
def build_items():
    print("Pulling Item Master ...")
    items, sectors, skipped = [], {}, 0
    for r in query_db(DB_ITEM_MASTER):
        p = r["properties"]
        sn = str(prop(p, "Serial Number")).strip()
        if not sn:
            skipped += 1; continue
        site = prop(p, "Site") or ""
        it = {"sn": sn,
              "title": prop(p, "Asset Identifier(Ref)"),
              "status": prop(p, "Status") or "Active",
              "model": prop(p, "Machine Type"),
              "type": prop(p, "Asset Type"),
              "sysno": str(prop(p, "System #")).strip(),
              "loc": prop(p, "Location/Block"),
              "site": site,
              "cluster": prop(p, "Cluster"),
              "addr": prop(p, "Address")}
        items.append(it)
        sec = prop(p, "HA Cluster")
        if site and sec and site not in sectors: sectors[site] = sec
    print(f"  {len(items)} items ({skipped} rows without serial skipped)")
    if len(items) < 500: sys.exit("FATAL validation: Item Master suspiciously small — aborting.")
    return items, sectors

def build_svc():
    print("Pulling CM records ...")
    svc = []
    for r in query_db(DB_CM):
        p = r["properties"]
        d = prop(p, "Date")
        site = prop(p, "Site")
        pic = aslist(prop(p, "PIC"))
        if not d and not site and not pic:   # empty 新的問題 stubs
            continue
        parts_raw = str(prop(p, "Parts no. (if used)") or "")
        parts = [x.strip() for x in re.split(r"[;\n,]+", parts_raw) if x.strip()]
        svc.append({"d": d or "", "site": site,
                    "mach": aslist(prop(p, "Machines Types")),
                    "sn": aslist(prop(p, "S/N")),
                    "pt": aslist(prop(p, "Problem Types")),
                    "err": aslist(prop(p, "Error code")),
                    "pic": pic,
                    "sym": aslist(prop(p, "Symptoms")),
                    "act": prop(p, "Actions Taken"),
                    "parts": parts,
                    "st": prop(p, "狀態"),
                    "l2": (lambda v: v[0] if isinstance(v, list) and v else (v or ""))(prop(p, "Problem L2")),
                    "rv": (lambda v: v[0] if isinstance(v, list) and v else (v or ""))(
                          prop(p, "Revisited within 7 days (same symptoms)"))})
    print(f"  {len(svc)} CM records")
    if len(svc) < 500: sys.exit("FATAL validation: CM records suspiciously few — aborting.")
    return svc

def build_pm():
    print("Pulling PM Master List ...")
    pm = []
    for r in query_db(DB_PM):
        p = r["properties"]
        pm.append({"site": prop(p, "End User"),
                   "d": prop(p, "Schedule Date"),      # real name has an embedded \n — norm() handles it
                   "item": prop(p, "Item Name"),
                   "sn": str(prop(p, "Serial Number")).strip(),
                   "pmno": prop(p, "PM no"),           # real name: "PM \nno"
                   "pic": aslist(prop(p, "Assigned to")),
                   "st": prop(p, "Status"),
                   "grp": prop(p, "Group"),
                   "addr": prop(p, "End User Address")})
    print(f"  {len(pm)} PM rows")
    return pm

PN_RE = re.compile(r"\b([A-Z0-9][A-Z0-9\-]{4,})\b")
def build_parts(svc):
    print("Deriving parts catalog from CM records ...")
    cat = {}
    for s in svc:
        for raw in s.get("parts", []):
            pn = next((m.group(1) for m in PN_RE.finditer(raw.upper())
                       if any(ch.isdigit() for ch in m.group(1))), "")
            name = raw
            if pn:
                name = re.sub(re.escape(pn), "", raw, flags=re.I).strip(" -–:()") or pn
            key = pn or name.lower()
            e = cat.setdefault(key, {"name": name[:60], "pn": pn, "n": 0, "l2": {}, "mach": []})
            e["n"] += 1
            if s.get("l2"): e["l2"][s["l2"]] = e["l2"].get(s["l2"], 0) + 1
            for mch in s.get("mach", []):
                if mch not in e["mach"]: e["mach"].append(mch)
    parts = sorted(cat.values(), key=lambda x: -x["n"])
    print(f"  {len(parts)} distinct parts")
    return parts

def build_cards():
    print("Pulling Solution Cards ...")
    cards = []
    for cid, title, catg, machines, keywords, page_id in CARDS:
        try:
            content = render_blocks(block_children(page_id))
        except SystemExit:
            raise
        cards.append({"id": cid, "title": title, "cat": catg,
                      "machines": machines, "keywords": keywords, "content": content})
        print(f"  card {cid}: {len(content)} chars")
    return cards

def build_errors():
    print("Pulling error-code tables ...")
    out = []
    for eid, name, note, page_id in ERROR_PAGES:
        groups, current_title = [], name
        for b in block_children(page_id):
            t = b.get("type")
            if t in ("heading_1", "heading_2", "heading_3"):
                current_title = rich(b[t].get("rich_text")) or current_title
            elif t == "table":
                rows = table_rows(b)
                if len(rows) >= 2:
                    groups.append({"title": current_title, "cols": rows[0], "rows": rows[1:]})
            elif t in ("toggle", "column_list", "column") and b.get("has_children"):
                for c in block_children(b["id"]):
                    if c.get("type") == "table":
                        rows = table_rows(c)
                        if len(rows) >= 2:
                            groups.append({"title": current_title, "cols": rows[0], "rows": rows[1:]})
        out.append({"id": eid, "name": name, "note": note, "groups": groups})
        print(f"  {eid}: {sum(len(g['rows']) for g in groups)} codes in {len(groups)} groups")
    return out

def build_procedures():
    print("Pulling procedure docs ...")
    docs = []
    for tag, page_id in PROCEDURE_DOCS:
        try:
            meta = api(f"/pages/{page_id}")
            tprop = next((v for v in meta["properties"].values() if v.get("type") == "title"), None)
            title = rich(tprop["title"]) if tprop else "Procedure"
            docs.append({"title": title.strip() or "Procedure", "tag": tag,
                         "md": render_blocks(block_children(page_id))})
            print(f"  doc: {title.strip()}")
        except SystemExit as e:
            print(f"  WARNING: procedure page {page_id} skipped ({e})")
    return {"docs": docs}

# ----------------------------------------------------------------------------
def leak_check(blob):
    low = blob.lower()
    for m in HARD_MARKERS:
        if m.lower() in low:
            sys.exit(f"FATAL leak check: marker '{m}' found in bundle — build aborted.")
    for m in SOFT_MARKERS:
        n = low.count(m.lower())
        if n:
            print(f"  WARNING leak check: soft marker '{m}' appears {n}x — review the source records.")

def main():
    if not TOKEN:
        sys.exit("FATAL: NOTION_TOKEN env var is empty. Set the repo secret.")
    hkt = timezone(timedelta(hours=8))
    items, sectors = build_items()
    svc = build_svc()
    bundle = {
        "asof": datetime.now(hkt).strftime("%Y-%m-%d %H:%M") + " HKT",
        "items": items, "sectors": sectors, "svc": svc,
        "pm": build_pm(), "parts": build_parts(svc),
        "cards": build_cards(), "errors": build_errors(),
        "procedures": build_procedures(),
    }
    blob = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    print(f"Bundle: {len(blob)/1e6:.2f} MB")
    leak_check(blob)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "bundle.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    print("OK: data/bundle.json written.")

if __name__ == "__main__":
    main()
