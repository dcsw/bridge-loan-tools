# Rye, NH: recommendation

Based on [tos-info.md](tos-info.md) (2026-11-06). Not legal advice.

## Recommendation

**Treat Rye as manual-drop only until the registry's terms are read and, if needed, clarified.** Rye is
not in Phase 1 or 2 anyway (the design sequences Rye after the Mattapoisett accuracy gate), so nothing
needs building now.

Reasons:
1. The Ava disclaimer says material may not be sold or given to third parties and that use of copies
   "for profit or not" may lead to legal action. That is a reason to ask before storing and displaying
   record contents in a tool, whether or not it is automated. Other terms pages were not retrievable.
2. Ava is a vendor-hosted app (Fidlar). Its own terms and bot protection are unknown.
3. Search is by name only (owner names come from the town, e.g. VGSI/Assessing), which makes a town-wide scrape impractical regardless of policy: you need an
   owner name first (from VGSI or the assessor).

## What to do

1. Read the full Ava disclaimer in a browser (https://www.nhdeeds.org/rockingham-county-other-nh-registry-list-2/)
   and the registry Disclaimer page; record anything beyond the summary in `tos-info.md`.
2. **Ask the registry** (Brentwood, NH; phone 603-642-5526 per the county site; Fidlar support is
   563-345-1283): is automated or bulk retrieval allowed, and is commercial use of the data permitted?
3. Until answered, use manual drops.

## Manual PDF drop

Where the PDFs come from (same as in tos-info.md):

- **Ava public search via https://www.nhdeeds.org/rockingham-home/** (grantor/grantee name, town = Rye,
  document type = Mortgage, then Discharge).
- Owner and parcel cross-reference: https://gis.vgsi.com/ryenh/ and https://www.town.rye.nh.us/assessing

Steps are the same as for Mattapoisett (see `../mattapoisett-ma/recommendation.md`): save the instrument PDF, then
`make manual-drop FILE=... URL="<ref>" TOWN=rye` (or `GAP=paid|blocked|failed` for one you could not get),
then `make fetcher-ingest`.

## What would change this recommendation

- Terms that expressly allow low-rate automated access, or written permission from the registry.
- A bulk or API option from the registry or Fidlar.
- Terms that forbid automation: manual drops remain the only path.
