# Madison, CT: portal terms and access findings

Reviewed 2026-11-06 by Claude from public web pages. **Not legal advice.** The portal's own terms were
not retrievable (it is behind Cloudflare), so there is no basis yet to say whether automation is permitted.

## Where the records are

Connecticut has no county records. Land records are filed with the **Madison Town Clerk**.

| System | URL | Notes |
|---|---|---|
| Madison Town Clerk | https://www.madisonct.org/153/Town-Clerk | Phone (203) 245-5672, townclerk@madisonct.org. |
| Land Records page | https://www.madisonct.org/459/Land-Records | States: "our online service, SearchIQS, is available to view indexes, and view/print images of land records." Gives the portal at the next row. |
| **SearchIQS: Madison portal** | https://www.searchiqs.com/ctmad/ | **Correction to our research notes**, which listed USLandRecords (https://www.uslandrecords.com/ctlr/). The town's own page names SearchIQS. |
| Fee schedule | https://www.madisonct.org/456/Fee-Schedule | Not retrieved. Image fees on SearchIQS not stated on the Land Records page. |
| Assessor | https://www.madisonct.org/155/Assessor | Terms not reviewed. |

## What the terms say

- Town site copyright: "All content © 2006-2026 Madison, CT and its representatives. All rights reserved."
  (Copyright Notices page /site/copyright has no reuse, commercial-use or automation terms in the text retrieved.)
- Registration for SearchIQS: the Land Records page says only that SearchIQS "will provide you with the
  information you need to navigate and subscribe". Subscription terms and prices are unknown.
- **No SearchIQS terms of use were retrieved.**

## Technical access controls and crawl policy

- A plain request to `https://www.searchiqs.com/ctmad/` returned **HTTP 403 "Just a moment..."**: a Cloudflare
  bot challenge requiring JavaScript and cookies. Passing it automatically would be circumventing an
  access control; this project will not do that.
- `https://www.searchiqs.com/robots.txt` (Cloudflare-managed) allows `/` for general crawlers but sets the
  content signal **`search=yes, ai-train=no, use=reference`** and blocks named AI/training crawlers
  (Amazonbot, Applebot-Extended and others). It is also an express reservation of rights against AI
  training use. Our use (extracting loan facts) is not "search" or training, so it is not clearly covered.
- `https://www.madisonct.org/robots.txt`: blocks admin and the town site's own search pages only.
- The previously listed USLandRecords portal (Avenu Insights & Analytics) shows only a copyright line and
  has no `robots.txt` (404); its terms were not found. It is probably not Madison's portal.

## Not verified

- SearchIQS Terms of Use and subscription agreement.
- Fee schedule for images (our notes estimate about $1-2 per page; unconfirmed).
- Whether guest access still gives a free index, and what the Cloudflare challenge allows for a human user.
- Connecticut Town Clerk rules on commercial reuse (needs counsel).

## Sources

- https://www.madisonct.org/153/Town-Clerk
- https://www.madisonct.org/459/Land-Records
- https://www.madisonct.org/site/copyright
- https://www.madisonct.org/robots.txt
- https://www.searchiqs.com/robots.txt and https://www.searchiqs.com/ctmad/ (direct requests, 2026-11-06)
- https://www.uslandrecords.com/ctlr/
- Project notes: `info/Free Sources for Bridge Loan Research — Rye NH, Madison CT, Mattapoisett MA.md`
