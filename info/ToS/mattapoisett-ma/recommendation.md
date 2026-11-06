# Mattapoisett, MA: recommendation

Based on [tos-info.md](tos-info.md) (2026-11-06). Not legal advice.

## Recommendation

**Do not build or run an automated (live) fetcher against Mattapoisett's record systems for now. Use
manual PDF drops.**

Reasons:
1. The registry prohibits **commercial use** of its material or online services without permission. This
   project's intended use may count as commercial; that needs an answer from the registry.
2. MassLandRecords actively blocks automated clients (Imperva Incapsula). The design's own stop/pivot
   rule applies: "if a portal's ToS forbids automation or CAPTCHAs block ... switch to manual PDF drop-folder
   for that town".
3. Plymouth now points users to **TitleView**, not MassLandRecords. The portal the design assumes may not
   be the right target, and TitleView's terms were not readable.

## What to do

1. **Ask the registry** (Plymouth County Registry of Deeds, customer service 508-830-9270; see
   https://www.plymouthdeeds.org/1233/Search-Records for the email form):
   - Which system is the authoritative public search for Plymouth records today (TitleView or MassLandRecords)?
   - Is automated or bulk retrieval permitted, and at what rate?
   - Does our use (a private lending-pattern research tool) count as "commercial use", and is there a
     permission, licence or bulk-data feed?
2. **Until answered, drop PDFs by hand** (below).
3. Keep Phase 2 live fetching blocked on the answers. If automation is allowed, the fetcher still needs
   the egress proxy with a domain allow-list from ADR-0003 before touching the network.

## Manual PDF drop

Where the PDFs come from (same as in tos-info.md):

- Primary: **https://www.titleview.org/plymouthdeeds** (linked from the registry's Search Records page).
- Alternate: **https://www.masslandrecords.com/** (statewide portal; advanced name search, town = Mattapoisett,
  document type = Mortgage, then Discharge for matching releases).
- Cross-reference owners/parcels: https://gis.vgsi.com/mattapoisettma/

Steps:
1. Search by hand in a normal browser and open each instrument; save or print the image to PDF. Free
   search is what the registry describes; any account-based printing is paid.
2. Note the instrument's URL or book/page as the source reference.
3. Put it in the drop directory with the helper (hashes the file, checks it is a PDF, writes
   `data/drop/<sha256>.pdf` plus the matching `<sha256>.json` manifest row, using the same code as the fetcher):
   ```bash
   make manual-drop FILE=~/Downloads/instrument.pdf URL="<instrument URL or book/page>" TOWN=mattapoisett
   # a document you could not get (e.g. a paid image you did not buy), so coverage shows the gap:
   make manual-drop GAP=paid URL="<reference>" TOWN=mattapoisett      # GAP=paid|blocked|failed
   ```
4. Run `make fetcher-ingest`, then the rest of the pipeline. Re-dropping the same bytes is a no-op.

## What would change this recommendation

- Written permission from the registry for automated or commercial use: then plan a rate-limited,
  identified fetcher behind the allow-listed egress proxy.
- A published bulk-data or API option: use it instead of scraping.
- TitleView terms that explicitly forbid automation: manual drops stay the only path.
