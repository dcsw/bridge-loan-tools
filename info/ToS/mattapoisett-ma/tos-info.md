# Mattapoisett, MA: portal terms and access findings

Reviewed 2026-11-06 by Claude from public web pages. **Not legal advice.** The binding Terms of Use
pages of the portals were not retrieved (see "Not verified"), so this records what was found, not a
conclusion that automation is permitted or prohibited.

## Where the records are

Mattapoisett is in Plymouth County. Recorded mortgages, deeds and discharges are at the
**Plymouth County Registry of Deeds**, which is county government (not the Secretary of the Commonwealth).

| System | URL | Notes |
|---|---|---|
| Plymouth Registry of Deeds, "Search Records" page | https://www.plymouthdeeds.org/1233/Search-Records | Directs users to **TitleView** for "free off-site access to Registry information". |
| TitleView (vendor-hosted) | https://www.titleview.org/plymouthdeeds | Could not be loaded during the review (connection reset), so its terms and behaviour are unknown. |
| MassLandRecords (statewide portal) | https://www.masslandrecords.com/ | The portal our design and `info/Free Sources...md` assume. Behind Imperva Incapsula bot protection (below). Whether it is still the authoritative system for Plymouth is **unconfirmed**. |
| Registry website disclaimer | https://www.plymouthdeeds.org/1283/Website-Disclaimer | The only registry-level terms text retrieved. |
| Assessor database (VGSI), used for parcel/owner cross-reference | https://gis.vgsi.com/mattapoisettma/ | Terms not reviewed. `robots.txt` returns 404 (no published crawl policy). |

## What the terms say

From the [Plymouth registry website disclaimer](https://www.plymouthdeeds.org/1283/Website-Disclaimer), quoted:

- "The Plymouth County Registry of Deeds provides the information on this website as a service to the public."
- "The Register's office makes no representations, guarantees, or warranties concerning the accuracy or reliability of the content at this site or at the other sites to which we link."
- "Assessing accuracy and reliability of information is the responsibility of the user."
- **"Commercial use of the material or our online services is prohibited without the permission of Plymouth County Registry of Deeds."**

The disclaimer does not mention automated access, scraping, bulk download or redistribution.
The site copyright line reads "All content © 2006-2026 Plymouth County Deeds, MA and its representatives.
All rights reserved." (The separate Copyright Notices page, /site/copyright, was not retrievable in full.)

The Search Records page does not state usage limits for free search. It says printing from home or office
"requires a deposit and a monthly fee" (an account). Copies can be ordered by phone, mail or credit-card form.

## Technical access controls and crawl policy

- `https://www.masslandrecords.com/robots.txt`: **404** (no crawl policy published).
- A single plain request to `https://www.masslandrecords.com/` with an ordinary User-Agent returned
  **"Request unsuccessful. Incapsula incident ID: ..."**: Imperva Incapsula bot protection is active.
  Defeating it would be circumventing an access control; this project will not do that.
- `https://www.plymouthdeeds.org/robots.txt`: disallows admin and the site's own search pages for all
  crawlers; says nothing about the records system, which is hosted elsewhere.

## Bulk data

No bulk-download, data-licence or API option was found on the registry pages that were read.

## Not verified

- The binding **Terms of Use / user agreement** of TitleView and MassLandRecords (not retrieved).
- Which of the two is the authoritative Plymouth system today.
- Registry's "Copyright Notices" and "Privacy Policy" pages in full.
- VGSI terms.
- Whether a commercial-use permission or licensed bulk feed exists (needs a question to the registry).
- Fees for image viewing or printing on TitleView.
- Massachusetts public-records law as applied to registry data reuse (needs counsel).

## Sources

- https://www.plymouthdeeds.org/1283/Website-Disclaimer
- https://www.plymouthdeeds.org/1233/Search-Records
- https://www.plymouthdeeds.org/site/copyright
- https://www.plymouthdeeds.org/robots.txt
- https://www.masslandrecords.com/ and /robots.txt (direct requests, 2026-11-06)
- Project notes: `info/Free Sources for Bridge Loan Research — Rye NH, Madison CT, Mattapoisett MA.md`
