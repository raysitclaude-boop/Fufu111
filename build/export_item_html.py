#!/usr/bin/env python3
"""
Static Item Master viewer.

Produces a single self-contained `item-master.html` that lists every asset
grouped by site — same look and grouping as the PWA's Sites view — but with
NO login, NO data bundle, NO network. Just open the file in a browser or host
it anywhere. Nothing sensitive is included (serials + machine types only, the
same fields the Site page shows).

Two ways to run:

  # from the nightly build output (keeps it current every run):
  python3 build/export_item_html.py            # reads data/bundle.json

  # from a plain JSON array of item rows (preview / one-off):
  python3 build/export_item_html.py rows.json  # [{site,sn,model,type,sysno,loc,status,cluster,ha}, ...]

Output: item-master.html in the current directory.
"""
import json, sys, html, datetime

HA_ORDER = ["Hong Kong Island", "Kowloon Central", "Kowloon East",
            "Kowloon West", "New Territories East", "New Territories West"]
_ALIAS = {"hong kong east": "Hong Kong Island", "hong kong west": "Hong Kong Island",
          "hk east": "Hong Kong Island", "hk west": "Hong Kong Island",
          "hk island": "Hong Kong Island", "nt east": "New Territories East",
          "nt west": "New Territories West", "kln central": "Kowloon Central",
          "kln east": "Kowloon East", "kln west": "Kowloon West"}

def sector(v):
    k = str(v or "").strip().lower()
    if not k:
        return ""
    return _ALIAS.get(k, str(v).strip())

def esc(s):
    return html.escape(str(s if s is not None else ""))

def load():
    """Return (items, sectors, asof)."""
    src = sys.argv[1] if len(sys.argv) > 1 else "data/bundle.json"
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):                       # build bundle
        items = data.get("items", [])
        sectors = {k: sector(v) for k, v in (data.get("sectors") or {}).items()}
        asof = data.get("asof", "")
    else:                                            # bare rows (preview)
        items = data
        sectors, asof = {}, ""
    # normalise + derive sector map from rows if not supplied
    norm = []
    for it in items:
        r = {"site": it.get("site") or "(no site)",
             "sn": it.get("sn") or "", "model": it.get("model") or "",
             "type": it.get("type") or "", "sysno": (it.get("sysno") or "").strip(),
             "loc": it.get("loc") or "", "status": it.get("status") or "Active",
             "cluster": it.get("cluster") or "", "ha": sector(it.get("ha"))}
        if r["sn"]:
            norm.append(r)
        if r["ha"] and r["site"] not in sectors:
            sectors[r["site"]] = r["ha"]
    return norm, sectors, asof or datetime.date.today().isoformat()

def sys_sort_key(k):
    # numeric-aware sort so #2 comes before #10; '— no system # —' last
    if k.startswith("—"):
        return (2, "", 0, k)
    import re
    m = re.search(r"\d+", k)
    return (0 if k.startswith("#") else 1, k.split("#")[0], int(m.group()) if m else 9999, k)

def status_tag(s):
    c = {"Active": "ok", "Retired": "bad"}.get(s, "warn")
    return f'<span class="tag {c}">{esc(s or "?")}</span>'

def item_line(i):
    bits = esc(i["model"] or i["type"])
    if i["sysno"]:
        bits += f' · <b style="color:var(--acc)">{esc(i["sysno"])}</b>'
    if i["loc"]:
        bits += f' · {esc(i["loc"])}'
    return (f'<div class="card"><div class="row"><h3>{esc(i["sn"])}</h3>{status_tag(i["status"])}</div>'
            f'<div class="dim">{bits}</div></div>')

def site_section(name, rows, sec):
    act = [r for r in rows if r["status"] != "Retired"]
    ret = [r for r in rows if r["status"] == "Retired"]
    by = {}
    for r in act:
        by.setdefault(r["sysno"] or "— no system # —", []).append(r)
    out = [f'<h2 id="{esc(anchor(name))}">{esc(name)}</h2>',
           f'<div class="dim">{("🗺️ " + esc(sec) + " · ") if sec else ""}{len(act)} active · {len(ret)} retired</div>']
    for k in sorted(by, key=sys_sort_key):
        out.append(f'<div class="sys">{esc(k)}</div>')
        out += [item_line(i) for i in by[k]]
    if ret:
        out.append('<div class="sys" style="border-color:var(--bad)">Retired / replaced</div>')
        out += [item_line(i) for i in ret]
    return "\n".join(out)

def anchor(name):
    return "s-" + "".join(c if c.isalnum() else "-" for c in name.lower())

def build():
    items, sectors, asof = load()
    sites = {}
    for r in items:
        sites.setdefault(r["site"], []).append(r)

    # group sites: HA (by cluster) → Private Hospital → Clinic → Other
    def cluster_of(site):
        c = (sites[site][0].get("cluster") or "").strip()
        return c or "Other"
    ha_sites, ph_sites, clinic_sites, other_sites = {}, [], [], []
    for site in sites:
        c = cluster_of(site)
        if c == "HA":
            ha_sites.setdefault(sectors.get(site) or "— cluster unknown —", []).append(site)
        elif c == "Private Hospital":
            ph_sites.append(site)
        elif c == "Clinic":
            clinic_sites.append(site)
        else:
            other_sites.append(site)

    groups = []  # (group label, [site names]) in display order
    for cl in HA_ORDER:
        if ha_sites.get(cl):
            groups.append((f"HA · {cl}", sorted(ha_sites[cl])))
    for extra in sorted(k for k in ha_sites if k not in HA_ORDER):
        groups.append((f"HA · {extra}", sorted(ha_sites[extra])))
    if ph_sites:
        groups.append(("Private Hospital", sorted(ph_sites)))
    if clinic_sites:
        groups.append(("Clinic / DC / Vet", sorted(clinic_sites)))
    if other_sites:
        groups.append(("Other", sorted(other_sites)))

    total = len(items)
    n_sites = len(sites)

    # top index with jump links
    idx = []
    for label, snames in groups:
        idx.append(f'<div class="sys">{esc(label)}</div>')
        for s in snames:
            n = len(sites[s]); a = sum(1 for r in sites[s] if r["status"] != "Retired")
            idx.append(f'<a class="idx" href="#{esc(anchor(s))}">{esc(s)} '
                       f'<span class="dim">· {a} active{("/" + str(n)) if n != a else ""}</span></a>')
    index_html = "\n".join(idx)

    # site sections
    sections = []
    for label, snames in groups:
        sections.append(f'<div class="grouphdr">{esc(label)}</div>')
        for s in snames:
            sections.append(site_section(s, sites[s], sectors.get(s)))
    sections_html = "\n".join(sections)

    doc = TEMPLATE.replace("{{ASOF}}", esc(asof)).replace("{{TOTAL}}", str(total)) \
                  .replace("{{NSITES}}", str(n_sites)).replace("{{INDEX}}", index_html) \
                  .replace("{{SECTIONS}}", sections_html)
    with open("item-master.html", "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"OK: item-master.html written — {total} assets across {n_sites} sites.")

TEMPLATE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FUJIFILM HK — Item Master by Site</title>
<style>
:root{--bg:#0f1720;--card:#1a2530;--line:#2a3947;--tx:#e8eef4;--dim:#8ba0b3;--acc:#4fc3f7;--ok:#66bb6a;--warn:#ffa726;--bad:#ef5350}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:15px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 14px;z-index:5}
header h1{font-size:17px}
header .sub{font-size:11.5px;color:var(--dim);margin-top:2px}
main{padding:12px 14px;max-width:820px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 13px;margin:8px 0}
.card h3{font-size:15px;margin-bottom:3px}
.dim{color:var(--dim);font-size:12.5px}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--dim);margin:2px 0 0}
.tag.ok{color:var(--ok);border-color:var(--ok)}.tag.bad{color:var(--bad);border-color:var(--bad)}.tag.warn{color:var(--warn);border-color:var(--warn)}
.row{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.sys{border-left:3px solid var(--acc);padding-left:10px;margin:14px 0 4px;font-weight:600}
.grouphdr{margin:26px 0 2px;font-size:13px;font-weight:700;color:var(--acc);letter-spacing:.3px;text-transform:uppercase;border-bottom:1px solid var(--line);padding-bottom:5px}
h2{font-size:16px;margin:18px 0 2px;scroll-margin-top:64px}
a.idx{display:block;color:var(--tx);text-decoration:none;padding:5px 8px;border-radius:8px;font-size:13.5px}
a.idx:hover{background:var(--card)}
details.toc{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0;padding:0}
details.toc>summary{padding:11px 14px;cursor:pointer;font-weight:600;list-style:none}
details.toc>summary::-webkit-details-marker{display:none}
details.toc .body{padding:4px 8px 12px}
.totop{position:fixed;right:14px;bottom:16px;background:var(--acc);color:#00222f;border-radius:99px;padding:9px 14px;text-decoration:none;font-weight:700;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,.4)}
@media print{
  :root{--bg:#fff;--card:#fff;--line:#bbb;--tx:#000;--dim:#444;--acc:#01579b;--warn:#e65100;--bad:#b71c1c;--ok:#1b5e20}
  header{position:static}.totop,details.toc{display:none}
  .card{break-inside:avoid}main{max-width:none}
}
</style></head><body>
<header><h1>🗂️ FUJIFILM HK — Item Master by Site</h1>
<div class="sub">{{TOTAL}} assets · {{NSITES}} sites · data as of {{ASOF}} · preview (not the live app)</div></header>
<main>
<details class="toc"><summary>Jump to a site ▾</summary><div class="body">{{INDEX}}</div></details>
{{SECTIONS}}
</main>
<a class="totop" href="#">↑ Top</a>
</body></html>"""

if __name__ == "__main__":
    build()
