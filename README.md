# 🧰 FUJI Field — offline PWA for field engineers

Read-only, offline-capable web app: item lookup by site/system, serial search with
service history, and troubleshooting Solution Cards. Data comes from Notion (single
source of truth) as an **encrypted snapshot** (`data.enc`) — nothing readable sits on
the host. Current snapshot: **2026-07-12** (599 items · 1,400 CM records · 11 cards),
password `fuji-field-2026` — **change it before rollout** (rebuild with your own FIELD_PW).

## Quick test (5 min, local)
```bash
cd pwa
python3 -m http.server 8000
# open http://localhost:8000 → password: fuji-field-2026
```
(Must be served over http(s) — opening index.html as a file:// won't work.)

## Deploy free (GitHub Pages, ~15 min one-time)
1. Create a **private** GitHub repo, put these files in it (`index.html`, `sw.js`,
   `manifest.json`, `data.enc`, `build/`). Do NOT commit `data/*.json` (raw, unencrypted).
2. Settings → Pages → deploy from branch → root. Your app URL appears.
3. Engineers open the URL on their phone → "Add to Home Screen" → enter password once
   (tick Remember). After first load it works fully **offline**; it refreshes data
   automatically whenever it reopens with signal.

## Nightly auto-refresh from Notion (optional, ~15 min one-time)
1. notion.so/my-integrations → New integration (internal) → copy token.
2. In Notion, on the CM database and Item Master List: ••• → Connections → add the integration.
   (This is the allowlist: the token can only see what you explicitly connect.)
3. Repo → Settings → Secrets → add `NOTION_TOKEN` and `FIELD_PW`.
4. Copy `build/nightly.yml` to `.github/workflows/nightly.yml`. Done — rebuilds 04:00 HKT daily.

## Security model (honest limits)
- Shared password, client-side AES-256-GCM (PBKDF2 150k). Anyone with URL+password sees
  everything in the bundle → the guarantee lives in **what goes in the bundle**:
  - Build **allowlists** exactly 3 sources (Item Master, CM records, Solution Cards).
    Hospital Specific Info / credentials are structurally excluded.
  - **Leak check** fails the build if credential patterns appear (`encrypt_bundle.py`).
    One real credential was already caught and scrubbed at first build.
- Someone leaves → change FIELD_PW, rebuild, push (1 minute). Old cached bundles on
  their device remain readable — acceptable per shared-password decision.
- Keep the repo **private** (data.enc is encrypted, but defence in depth).

## Files
| File | Purpose |
|---|---|
| index.html | entire app (no framework, no CDN — fully offline) |
| sw.js | service worker: cache-first shell, network-first data |
| data.enc | encrypted bundle: salt(16)+iv(12)+AES-GCM |
| build/pull_notion.py | Notion → data/*.json (NOTION_TOKEN) |
| build/encrypt_bundle.py | data/*.json → data.enc + leak check (FIELD_PW) |
| build/nightly.yml | GitHub Action for daily refresh |

## Known v1 limits (by design, per architecture spec)
- Service history keyed on CM `S/N` values — records missing S/N won't show under a serial.
- KPI screen deliberately absent (T4 decision: own-metrics view can come in v2 via per-engineer builds).
- PM schedule screen waits for PM Visit Log data (v2).
