# Madison, CT: recommendation

Based on [tos-info.md](tos-info.md) (2026-11-06). Not legal advice.

## Recommendation

**Manual-drop only, and do not automate.** Madison is the weakest candidate for automation:

1. The portal (SearchIQS) is behind a Cloudflare bot challenge, and its robots.txt reserves rights
   against AI/training use.
2. Images are paid (per the design: "CT paid images need a per-run budget"), and subscription terms are unknown.
3. Rye and Madison come after the Mattapoisett accuracy gate in the design, so nothing is needed now.

## What to do

1. **Fix our source list:** replace USLandRecords with SearchIQS (https://www.searchiqs.com/ctmad/) in
   `info/Free Sources...md` and any design text that assumes USLandRecords.
2. **Ask the Town Clerk** (203-245-5672, townclerk@madisonct.org): what the SearchIQS subscription costs,
   whether automated or bulk retrieval is allowed, whether commercial use is permitted, and whether the
   town offers bulk or licensed land-records data.
3. Read the SearchIQS terms in a browser (the challenge needs a person) and record them in `tos-info.md`.

## Manual PDF drop

Where the PDFs come from (same as in tos-info.md):

- **https://www.searchiqs.com/ctmad/** (Madison land records; index since 1974, images paid per the town page and our notes).
- Owner/parcel cross-reference: https://www.madisonct.org/155/Assessor

Steps are the same as for Mattapoisett (see `../mattapoisett-ma/recommendation.md`): save the instrument PDF, then
`make manual-drop FILE=... URL="<ref>" TOWN=madison` (or `GAP=paid|blocked|failed` for one you could not get),
then `make fetcher-ingest`.
Budget the per-page image fees before buying images for a batch.

## What would change this recommendation

- Written permission or a licensed bulk feed from the Town Clerk or SearchIQS.
- SearchIQS terms that expressly allow low-rate automated access (the bot challenge would still need a
  sanctioned API or allow-listed client, not a workaround).
