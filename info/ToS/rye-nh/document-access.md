# Rye, NH: getting the documents (manual-drop findings)

Written 2026-11-07. Not legal advice. Companion to [tos-info.md](tos-info.md) and [recommendation.md](recommendation.md).

## Status

**No PDFs have been obtained for Rye.** On the first hand attempt, the user could not find any PDF to
download in the Rockingham County Registry of Deeds search (Ava).

## What is known (unverified against the live screens)

- The registry names two systems: **Ava - Public Search** and **Tapestry - Pay as you go**
  (https://www.nhdeeds.org/rockingham-home/).
- Ava needs the disclaimer accepted, no login. The index is searched **by name** (grantor/grantee).
  There is no address lookup at the registry; owner names come from the town.
- The most likely explanation (an inference, not confirmed): free Ava search returns index rows
  (names, document type, date, book/page) and the document images are behind Tapestry or paid copies.
- The registry accepts only cash or check. Copy-fee amounts were not retrieved.
- The earlier how-to said "open the image and save or print to PDF". That step was written without
  having seen Ava and may not be possible.

## Open question: what Ava actually shows

Record which of these applies, then update this file:

1. No image or view button; index only.
2. A view button that asks for payment or an account (Tapestry).
3. An image viewer with no save or print option.

## Options

| Option | Notes |
|---|---|
| Record a gap | `make manual-drop GAP=paid URL="Rockingham book N page N" TOWN=rye` (`GAP=paid\|blocked\|failed`). Coverage then reports Rye as unobtainable rather than silently missing. |
| Ask the registry | Rockingham 603-642-5526. Ask: fee for a mortgage image, whether a PDF can be provided, and whether storing record contents in a private research tool is allowed (see the disclaimer's resale / third-party and "for profit or not" clauses in tos-info.md). |
| Use a town with free images | Mattapoisett via MassLandRecords (images usually free; commercial-use clause unanswered). Madison CT via the Town Clerk (203-245-5672). See their folders under `info/ToS/`. |
| Blank sample forms | Public sample or uniform NH mortgage forms would exercise the long-legal-text layout for parser work (WU13), but contain no real borrower data. |

## Impact on the plan

- Phase 2 needs at least 100 documents; Rye cannot supply them until image access is resolved.
- WU13 (real-document hardening, bead `.14`) needs real-format PDFs from some source.
- Live fetching (WU14, bead `.15`) stays blocked by the ToS review.

## Owner-name sources (for index searches)

- Rye assessor database (Vision/VGSI): https://gis.vgsi.com/ryenh/
- Town of Rye Assessing: https://www.town.rye.nh.us/assessing
