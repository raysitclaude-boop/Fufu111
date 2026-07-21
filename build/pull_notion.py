#!/usr/bin/env python3
"""
pull_notion.py — nightly data pull for the FUJI Field PWA (V5 bundle).

V5 change (2026-07-19): PM rows now include the Notion page id ("id") so the
app can tick a schedule row as Completed through the write proxy.

Runs inside GitHub Actions:

    env NOTION_TOKEN  = Notion internal-integration token (repo secret)
    output            = data/bundle.json   (raw, NEVER committed — encrypt_bundle.py
                        turns it into data.enc + dataw.enc, workflow deletes data/*.json)

Design rules (KB_PWA_Architecture_Spec.md §2.3, binding):
  * ALLOWLIST: only the data sources / pages enumerated below are pulled.
  * Token never written anywhere.
  * Leak check on output: hard-fail on high-confidence credential markers,
    loud warning on soft markers (so a stray word can't brick the nightly build).

Bundle schema (must match index.html):
  asof, items[], sectors{}, svc[], pm[], parts[], cards[], errors[], procedures{}

Stdlib only (urllib) — `cryptography` is only needed by encrypt_bundle.py.
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

def prop_like(props, *subs):
    """Find a property by SUBSTRING of its normalised name, trying subs in order.
    Monthly CSVs rename columns between releases — e.g. the June file has
    'Assigned to' while the June_Kenton file has
    'Main Person In Charge (Main PIC(s))\\n①/+②'. Without this, PICs vanish."""
    for s in subs:
        for k, v in props.items():
            if s in norm(k):
                val = pval(v)
                if val not in ("", None, []): return val
    return ""

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
# HA clusters — the official Hospital Authority list has SIX. Hong Kong East and
# Hong Kong West were merged into Hong Kong Island. Older Notion rows (and any
# hand-typed value) may still use retired or abbreviated names, so normalise on
# the way in and keep the app's grouping keys stable.
# ----------------------------------------------------------------------------
HA_CLUSTERS = ["Hong Kong Island", "Kowloon Central", "Kowloon East",
               "Kowloon West", "New Territories East", "New Territories West"]
_SECTOR_ALIAS = {
    "hk island": "Hong Kong Island", "hong kong island": "Hong Kong Island",
    "hong kong east": "Hong Kong Island", "hong kong west": "Hong Kong Island",
    "hk east": "Hong Kong Island", "hk west": "Hong Kong Island",
    "kowloon central": "Kowloon Central", "kln central": "Kowloon Central",
    "kowloon east": "Kowloon East", "kln east": "Kowloon East",
    "kowloon west": "Kowloon West", "kln west": "Kowloon West",
    "nt east": "New Territories East", "new territories east": "New Territories East",
    "nt west": "New Territories West", "new territories west": "New Territories West",
}
def HA_SECTOR(v):
    k = str(v or "").strip().lower()
    if not k:
        return ""
    return _SECTOR_ALIAS.get(k, str(v).strip())

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
        # Sector map: first NON-EMPTY value wins. (Previously first-seen won, so a
        # site whose first row had a blank HA Cluster stayed unmapped forever.)
        sec = HA_SECTOR(prop(p, "HA Cluster"))
        if site and sec and not sectors.get(site): sectors[site] = sec
    print(f"  {len(items)} items ({skipped} rows without serial skipped)")
    if len(items) < 500: sys.exit("FATAL validation: Item Master suspiciously small — aborting.")

    # Guard: every HA site must land in a cluster, or it renders as uncategorised
    # in the app. Fail loudly at build time instead of shipping a silent gap.
    ha_sites = {i["site"] for i in items if i.get("cluster") == "HA" and i.get("site")}
    unmapped = sorted(s for s in ha_sites if not sectors.get(s))
    if unmapped:
        sys.exit("FATAL validation: HA site(s) with no HA Cluster — fix in Notion "
                 "(Item Master → HA Cluster) then re-run:\n  - " + "\n  - ".join(unmapped))
    bad = sorted({v for v in sectors.values() if v not in HA_CLUSTERS})
    if bad:
        print(f"  WARNING: non-standard cluster name(s) in use: {bad}")
    print(f"  {len(sectors)} sites mapped to clusters")
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

# ---------------------------------------------------------------------------
# PM source (V5.1): the official reference is the MONTHLY release database
# R imports at the end of each month, e.g. "20260601_PM Schedule for Jun_Update.csv".
# Each release is a NEW Notion database, so the build discovers them by title
# pattern via the Notion search API (integration only sees what is shared with
# it — share the parent page once and every monthly import under it is found).
# The newest PM_RELEASES_TO_MERGE releases are merged (newest wins on the same
# serial+date) so the boss report keeps ~3 months of history.
# Fallback: if no release is found, use the old PM Master List with a window.
# ---------------------------------------------------------------------------
PM_TITLE_RE = re.compile(r"^(\d{8})_PM Schedule", re.I)   # 20260601_PM Schedule for Jun_Update
PM_RELEASES_TO_MERGE = 12
PM_MONTHS_BACK = 3   # fallback window only
PM_MONTHS_FWD  = 6

def _month_shift(dt, months):
    y, m = dt.year, dt.month + months
    y += (m - 1) // 12; m = (m - 1) % 12 + 1
    return f"{y:04d}-{m:02d}"

def find_monthly_pm_dbs():
    """All shared databases whose title matches the monthly-release pattern,
    newest first (by the YYYYMMDD filename prefix)."""
    out, cursor = [], None
    while True:
        payload = {"query": "PM Schedule",
                   "filter": {"property": "object", "value": "database"},
                   "page_size": 100}
        if cursor: payload["start_cursor"] = cursor
        res = api("/search", payload)
        for r in res.get("results", []):
            if r.get("object") != "database": continue
            title = "".join(t.get("plain_text", "") for t in r.get("title", [])).strip()
            m = PM_TITLE_RE.match(title)
            if m: out.append((m.group(1), r["id"], title))
        if not res.get("has_more"): break
        cursor = res.get("next_cursor")
    out.sort(reverse=True)
    return out

def parse_sched_date(s):
    """Monthly releases store Schedule Date as text: '30-6月-2026', '2026-06-30',
    or '30/6/2026'. Return ISO YYYY-MM-DD, or '' if unparseable."""
    s = str(s or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m: return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})月[-/](\d{4})", s)
    if m: return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m: return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return ""

ROSTER = ["Anson", "Poon", "Pong", "Alan", "Yoshida", "Ivan", "Ray", "Joe",
          "Eunice", "Tai", "Kenton", "Tim", "Winnie", "Cheryl", "Dixon", "Choi"]
ROSTER_L = {n.lower(): n for n in ROSTER}

def canon_name(n):
    """One identity per engineer: 'poon' -> 'Poon', 'Alan*' -> 'Alan'.
    The trailing asterisk is a note marker in the source sheet, not a person."""
    s = str(n or "").strip().strip("*＊").strip()
    if not s: return ""
    return ROSTER_L.get(s.lower(), s[:1].upper() + s[1:].lower())

def split_names(v):
    """'Assigned to' is plain text in CSV imports ('Ray, Joe') but multi-select
    in the master list — accept both, and canonicalise every name."""
    raw = v if isinstance(v, list) else re.split(r"[,;/、+&\s]+", str(v or ""))
    out = []
    for x in raw:
        c = canon_name(x)
        if c and c not in out: out.append(c)
    return out

MON_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MON_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December"
    r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)", re.I)

def release_kind(datecode, target_ym):
    """Two kinds of monthly file, per R's workflow:
      'schedule' — released at/just before the month starts: the assignment list
                   (who does which PM this month). Defines the work + the PIC.
      'check'    — released DURING or AFTER the month: the verified completion
                   record compiled from paper returns. Not every engineer uses
                   the PWA, so THIS is the official completion source for KPI.
    Rule: released after the target month began -> it's a check list."""
    try:
        rel_ym = f"{datecode[:4]}-{datecode[4:6]}"
    except Exception:
        return "schedule"
    return "check" if rel_ym > target_ym else "schedule"

def release_label(datecode, title=""):
    """Which month is this release FOR?

    The filename date is the RELEASE date, not the target month:
      20260601_PM Schedule for Jun_Update  -> released 1 Jun, for June
      20260630_PM Schedule_July            -> released 30 Jun, for JULY
      20260713_PM Schedule_June_Kenton     -> released 13 Jul, for JUNE
    So the month NAME in the title wins; the datecode is only a fallback.
    The year is inferred from the datecode (handles a Dec release for January).
    """
    y, dm = None, None
    try:
        y, dm = int(datecode[:4]), int(datecode[4:6])
    except Exception:
        return title or datecode
    m = MON_RE.search(re.sub(r"^\d{8}", "", title or ""))
    if m:
        name = m.group(1)[:3].title()
        tm = MON_ABBR.index(name) + 1 if name in MON_ABBR else dm
        # released in Dec for Jan (or in Jan for Dec) → roll the year
        if dm == 12 and tm == 1: y += 1
        elif dm == 1 and tm == 12: y -= 1
        return f"{MON_ABBR[tm - 1]} {y}"
    return f"{MON_ABBR[dm - 1]} {y}"

def label_to_ym(label):
    """'Jun 2026' -> '2026-06' (sortable)."""
    try:
        a, y = label.split()
        return f"{int(y):04d}-{MON_ABBR.index(a) + 1:02d}"
    except Exception:
        return ""

def _pm_row(r, rel="", src="schedule"):
    p = r["properties"]
    raw_d = prop(p, "Schedule Date")             # matches 'Schedule\nDate' too (norm())
    return {"rel": rel,                          # which monthly release this row came from
            "relm": label_to_ym(rel),            # sortable YYYY-MM of that release
            "src": src,                          # 'schedule' | 'check' (official completion)
            "id": r["id"],                       # needed for tick-off writes
            "site": prop(p, "End User"),
            "d": parse_sched_date(raw_d) or (raw_d if isinstance(raw_d, str) else ""),
            "item": prop(p, "Item Name"),
            "sn": str(prop(p, "Serial Number")).strip(),
            "pmno": prop(p, "PM no"),            # real name: "PM \nno"
            # column name varies by release file — see prop_like()
            "pic": split_names(prop(p, "Assigned to")
                               or prop_like(p, "person in charge", "pic(s)", "pic")),
            "picsrc": src,                       # where the PIC came from
            "chk": split_names(prop(p, "Checked By")),
            "cd": prop_like(p, "completed date"),
            "st": prop(p, "Status"),
            "grp": prop(p, "Group"),
            "addr": prop(p, "End User Address")}

def _pm_blank(row):
    """A CSV import leaves trailing all-empty rows behind. They carry no machine,
    no site and no serial, but still counted toward '6/19 done' and cluttered the
    schedule. A row is real only if it identifies WHAT is being serviced (serial
    or item) or WHERE (site)."""
    return not (str(row.get("sn") or "").strip()
                or str(row.get("item") or "").strip()
                or str(row.get("site") or "").strip())

def build_pm():
    print("Looking for monthly PM Schedule releases ...")
    rels = find_monthly_pm_dbs()
    if not rels:
        print("  WARNING: no 'YYYYMMDD_PM Schedule…' database shared with the "
              "integration — falling back to the PM Master List.")
        return build_pm_master()
    merged, dup = {}, [0]
    blank = [0]        # all-empty rows left behind by the CSV import
    by_serial = {}     # (release month, serial) -> [keys], for the fallback match
    claimed = set()    # keys a check-list row has already completed
    pmno_fix = [0]     # completions rescued by matching on serial instead of PM no
    use = rels[:PM_RELEASES_TO_MERGE]
    for datecode, dbid, title in use[::-1]:      # oldest → newest, newest wins
        rows = query_db(dbid)
        label = release_label(datecode, title)
        kind = release_kind(datecode, label_to_ym(label))
        print(f"  {kind:8} {title} -> '{label}': {len(rows)} rows")
        for r in rows:
            row = _pm_row(r, label, kind)
            if _pm_blank(row):
                blank[0] += 1
                continue
            # Key on RELEASE MONTH + serial + PM number — never the schedule date:
            # the July and check-list CSVs ship with the date column empty, so a
            # date-based key would duplicate rows instead of updating them.
            sn = row["sn"].upper()
            key = (row["relm"], sn or r["id"], str(row["pmno"] or ""))
            old = merged.get(key)
            # A check list is compiled by hand from the paper returns, and its
            # "PM no" regularly disagrees with the schedule's for the same machine
            # (Macau, June 2026: schedule says 2/4, check list says 4/4). Matching
            # on PM no alone files the completion as a SECOND row and leaves the
            # scheduled one outstanding for ever. So when the exact key misses,
            # claim an unmatched schedule row with the same serial in the same
            # month — the serial identifies the machine, the PM no does not.
            if old is None and kind == "check" and sn:
                for k in by_serial.get((row["relm"], sn), []):
                    if k in claimed or merged[k]["src"] != "schedule":
                        continue
                    key, old = k, merged[k]
                    pmno_fix[0] += 1
                    break
            if old:
                claimed.add(key)
                dup[0] += 1
                # Field-wise merge: a check list usually carries only status +
                # checker, so keep the schedule's date/PIC/address rather than
                # blanking them. Ticking must still target the schedule row, so
                # the original id is preserved.
                m = dict(old)
                for k, v in row.items():
                    if v not in ("", [], None): m[k] = v
                if row["src"] == "check":
                    m["id"] = old["id"]
                    m["st"] = row["st"]           # official status wins outright
                    # PIC: the schedule holds the PRE-ASSIGNED engineer, but jobs
                    # get reassigned in the field. The check list is built from the
                    # official service record, so it holds the ACTUAL engineer and
                    # wins. Keep the original assignment as 'apic' when it differs,
                    # so reassignments stay visible instead of silently vanishing.
                    if row["pic"]:
                        m["picsrc"] = "check"
                        if old.get("pic") and old["pic"] != row["pic"]:
                            m["apic"] = old["pic"]
                    else:
                        m["pic"] = old.get("pic", [])      # no actual PIC recorded
                        m["picsrc"] = old.get("picsrc", "schedule")
                merged[key] = m
            else:
                merged[key] = row
                if sn:
                    by_serial.setdefault((row["relm"], sn), []).append(key)
    pm = list(merged.values())
    nodate = sum(1 for x in pm if not x["d"])
    bystat = {}
    for x in pm:
        k = f'{x["relm"] or "?"} {x["rel"]} [{x["src"]}] / {x["st"] or "(no status)"}'
        bystat[k] = bystat.get(k, 0) + 1
    nopic = sum(1 for x in pm if not x["pic"])
    reasgn = sum(1 for x in pm if x.get("apic"))
    actual = sum(1 for x in pm if x.get("picsrc") == "check")
    print(f"  PIC: {actual} rows from the official check-list (actual engineer), "
          f"{len(pm) - actual} from the schedule (pre-assigned); {reasgn} reassigned")
    print(f"  {len(pm)} PM rows merged from {len(use)} release(s); {dup[0]} rows updated by a later file"
          + (f"; {blank[0]} empty import rows dropped" if blank[0] else "")
          + (f"; {pmno_fix[0]} completions matched on serial (PM no disagreed "
             f"between schedule and check list)" if pmno_fix[0] else "")
          + (f"; {nodate} with no schedule date" if nodate else "")
          + (f"; {nopic} with no PIC" if nopic else ""))
    for k in sorted(bystat): print(f"    {k}: {bystat[k]}")
    return pm

def build_pm_master():
    print("Pulling PM Master List (fallback) ...")
    today = datetime.now(timezone(timedelta(hours=8)))
    lo = _month_shift(today, -PM_MONTHS_BACK)
    hi = _month_shift(today, PM_MONTHS_FWD)
    pm, skipped = [], 0
    for r in query_db(DB_PM):
        row = _pm_row(r)
        if row["d"] and not (lo <= row["d"][:7] <= hi):
            skipped += 1; continue
        pm.append(row)
    print(f"  {len(pm)} PM rows in window {lo}..{hi} ({skipped} outside window skipped)")
    return pm

# --- Parts parsing ----------------------------------------------------------
# Engineers record parts as one free-text string, e.g.
#     "BCN65A Board Assembly P/N: 857Y120043C"
#     "RMV65A Board: P/N: 857Y200090"
# The old parser grabbed the first token containing a digit, which matched the
# BOARD CODE (BCN65A) rather than the part number, and left the "P/N:" label
# stranded in the name — producing " Board Assembly P/N: 857Y120043C".
#
# Rules now:
#   1. Split on the P/N label (P/N, PN, P/M typo) — everything after it is the number.
#   2. Board codes (3 letters + 2 digits + 1 letter) belong to the NAME, and are
#      moved to the end so one board reads the same way everywhere:
#         "BCN65A Board Assembly"  ->  "Board Assembly BCN65A"
#         "RMV65A Board"           ->  "Board RMV65A"
PN_LABEL_RE = re.compile(r"\b(?:P\s*/\s*N|PN|P\s*/\s*M)\b\s*:?\s*", re.I)
BOARD_CODE_RE = re.compile(r"\b([A-Z]{3}\d{2}[A-Z])\b")
BARE_PN_RE = re.compile(r"\b(ACC\d{4}|[A-Z0-9]{2,}[-_][A-Z0-9\-_]{4,})\s*$", re.I)

def parse_part(raw):
    """'BCN65A Board Assembly P/N: 857Y120043C' -> ('Board Assembly BCN65A', '857Y120043C')"""
    s = str(raw).strip()
    m = PN_LABEL_RE.search(s)
    if m:
        name, pn = s[:m.start()], s[m.end():].strip()
    else:
        m2 = BARE_PN_RE.search(s)          # no label, but a trailing ACCnnnn / dashed code
        if m2:
            name, pn = s[:m2.start()], m2.group(1).strip()
        else:
            name, pn = s, ""
    pn = re.sub(r"\s+", " ", pn).strip().rstrip(".,;")

    name = re.sub(r"\s{2,}", " ", name).strip().strip(":").strip()
    cm = BOARD_CODE_RE.search(name)
    if cm:
        code = cm.group(1)
        rest = (name[:cm.start()] + " " + name[cm.end():]).strip()
        paren = ""
        pm = re.search(r"\(([^)]*)\)", rest)
        if pm:
            inner = pm.group(1).strip()
            if inner:
                paren = " (" + inner + ")"
            rest = (rest[:pm.start()] + " " + rest[pm.end():]).strip()
        rest = re.sub(r"\s{2,}", " ", rest).strip().strip(":").strip()
        rest = re.sub(r"\bboard\b", "Board", rest)
        name = f"{rest} {code}{paren}" if rest else f"Board {code}"
    return re.sub(r"\s{2,}", " ", name).strip(" -–:"), pn

def build_parts(svc):
    print("Deriving parts catalog from CM records ...")
    cat = {}
    for s in svc:
        for raw in s.get("parts", []):
            name, pn = parse_part(raw)
            if not name and pn:
                name = pn
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
