# 🧰 FUJI Field — offline PWA for field engineers (v3)

Read-only, offline-capable web app: item lookup by site/system, serial search with service
history, troubleshooting (symptoms + error-code index + common procedures), parts, PM, and a
team Analysis screen. Data comes from Notion (single source of truth) as an **encrypted
snapshot** (`data.enc`). Current snapshot: **2026-07-14** (455 items · 1,400 CM records ·
11 cards · 104 parts · 155 PM rows · 3 error-code machines / 357 codes · 5 procedures),
— **change it before rollout** (rebuild with your own FIELD_PW).

## What changed in v3
- **Sites/Field sorting fixed.** One canonical cluster per site now, so HA hospitals never
  leak into "Other" (Buddhist, Prince of Wales, Princess Margaret, United Christian + 29
  others recovered). Overrides: Precious Blood → Private Hospital, Medtimes Sha Tin → Clinic,
  HK Adventist → Private Hospital. **144 items with no site were dropped** (599 → 455).
  Logic lives in `build/normalize.py` and runs every nightly build.
- **KPI → Analysis.** Rebuilt to match the full CM Data Analysis deck, with a section switcher
  (Overview / Sites / People / Problems / Root cause / Quality) and a 2025 / 2026 / All year
  filter. Covers: headline KPIs, monthly CM trend (SVG line), problem-type share, CM-by-hospital,
  CM-by-engineer, solo-vs-collaborative + top pairs, machines, top symptoms, error-code frequency,
  root-cause (Problem L2), most-used parts, revisit-within-7-days (rate + symptoms), last-3-months
  volume, and auto key takeaways. All charts are inline SVG/CSS — no CDN, fully offline, computed
  live from the bundle. **Revisit charts** need the `Revisited within 7 days (same symptoms)`
  field, now added to `pull_notion.py`; they activate on the first nightly rebuild (until then the
  Quality tab shows a notice instead of a guessed number).
- **Fix page split into 3 tabs:**
  1. **By Symptom** — the existing Solution Cards + past-case search.
  2. **Error Code Index** — hand-copied Go Plus / FDR nano (DR-XD1000) / D-EVO3 (DR-ID 1800)
     error tables from Notion. Machine chips + search by code, name, or remedy.
  3. **Common Procedures** — Detector Calibration values (chart), QA Procedures & Calibration
     Methods (FULL) incl. EI/DI + IEC60601 safety, DAP calibration (Go Plus), and the
     Network / Hardware / Software Troubleshooting Playbooks.

> Note on the playbooks (you asked what to include): I kept the three condensed one-page
> playbooks — Network is the most actionable (ping/arp/tracert commands, IP-conflict drill,
> WL/PACS + Wi-Fi fixes, real case notes); Hardware and Software are the fewest-step diagnosis
> flows + symptom→direction + common entry points. Deep step-by-step reference (photos, board
> pinouts) stays in Notion. Easy to trim or expand any section — tell me which.

## Quick test (5 min, local)
```bash
cd v3
python3 -m http.server 8000
# open http://localhost:8000 → password: fuji-field-2026
```
(Must be served over http(s) — opening index.html as a file:// won't work.)

## Deploy free (GitHub Pages, ~15 min one-time)
1. Create a **private** repo; put `index.html`, `sw.js`, `manifest.json`, `data.enc`, `build/`.
   Do NOT commit `data/*.json` (raw, unencrypted — excluded from this zip on purpose).
2. Settings → Pages → deploy from branch → root.
3. Engineers open the URL on their phone → "Add to Home Screen" → enter password once
   (tick Remember). Works fully offline after first load; refreshes data when reopened online.

## Nightly auto-refresh from Notion (optional)
1. notion.so/my-integrations → New internal integration → copy token.
2. In Notion, connect the integration to the CM database + Item Master List (••• → Connections).
3. Repo → Settings → Secrets → add `NOTION_TOKEN` and `FIELD_PW`.
4. Copy `build/nightly.yml` to `.github/workflows/nightly.yml`. Rebuilds 04:00 HKT daily.
   The build runs `pull_notion.py` → `encrypt_bundle.py` (which applies `normalize.py` and
   re-attaches the static error tables + procedures).

## Files
| File | Purpose |
|---|---|
| index.html | entire app (no framework, no CDN — fully offline) |
| sw.js | service worker: cache-first shell, network-first data (cache `fujifield-v3`) |
| data.enc | encrypted bundle: salt(16)+iv(12)+AES-256-GCM |
| build/pull_notion.py | Notion → data/*.json (NOTION_TOKEN) |
| build/normalize.py | v3 site-cluster fix + blank-site drop (shared by the build) |
| build/encrypt_bundle.py | data/*.json + static → data.enc + leak check (FIELD_PW) |
| build/static/errors.json | Error Code Index (Go Plus / nano / D-EVO3) |
| build/static/procedures.json | Common Procedures (calibration / QA / DAP / playbooks) |
| build/static/pm.json, sectors.json | PM schedule + HA sector map |
| build/nightly.yml | GitHub Action for daily refresh |

## Security model (unchanged from v2)
Shared password, client-side AES-256-GCM (PBKDF2 150k). Build allowlists only Item Master, CM
records, Solution Cards; Hospital Specific Info / credentials are structurally excluded and a
leak check fails the build on credential patterns. Someone leaves → change FIELD_PW, rebuild,
push. Keep the repo private (defence in depth).

## To regenerate error tables / procedures (source of truth)
Static content was extracted from these Notion pages — re-run extraction if they change:
- Error tables: Resource - FUJI 官方Manual → (Go Plus) Error code + Part list 總表; FDR nano
  (DR-XD1000) Error Code Table; (Devo3) DR-ID 1800 Error code.
- Procedures: SOP - Fuji Procedures → QA/Safety/Calibration → Detector Calibration - Chart;
  QA Procedures & Calibration Methods (FULL); DAP calibration (GoPlus); plus the Network /
  Hardware / Software Troubleshooting Playbooks.
