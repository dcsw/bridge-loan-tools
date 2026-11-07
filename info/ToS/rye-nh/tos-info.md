# Rye, NH: portal terms and access findings

Reviewed 2026-11-06 by Claude from public web pages. **Not legal advice.** Updated later the same day
after finding the Ava disclaimer (below), which is stricter than Mattapoisett's.

## Where the records are

New Hampshire records at the county level. Rye is in Rockingham County; recorded mortgages and
discharges are at the **Rockingham County Registry of Deeds**.

| System | URL | Notes |
|---|---|---|
| Rockingham Registry of Deeds home | https://www.nhdeeds.org/rockingham-home/ | Names two systems: "Ava - Public Search" and "Tapestry - Pay as you go". Lists Fidlar Technologies for technical support. |
| Searching-records help | https://www.nhdeeds.org/rockingham-county-searching-records/ | Index is by name (grantor/grantee); a name is needed to start a search. Per the page, there is no way to find owners from a property address at the registry: ask the town. |
| Important Notice | https://www.nhdeeds.org/rockingham-important-notice/ | Only says index entries 1882-1919 for some document types must be viewed at the registry. |
| Disclaimer, Copy Fees, Recording Fees | linked from the home page; **not retrieved** (one guessed URL returned 404) | Likely holds any use restrictions. |
| Rye assessor (Vision/VGSI), for owner/parcel cross-reference | https://gis.vgsi.com/ryenh/ | Terms not reviewed. `robots.txt` returns 404. |
| Town of Rye Assessing | https://www.town.rye.nh.us/assessing | `robots.txt` blocks only some search/admin paths. |

## What the terms say

**Ava disclaimer** (must be accepted before the index can be searched; page
https://www.nhdeeds.org/rockingham-county-other-nh-registry-list-2/, then
https://www.nhdeeds.org//rockingham-county-index/). Paraphrased and quoted from the page text retrieved:

- "ALL INFORMATION SHOWN IS FOR REFERENCE ONLY AND NOT A SUBSTITUTE FOR THE ACTUAL REGISTRY INDICES AND/OR DOCUMENTS."
- "Only copies obtained from the official Registry of Deeds records are considered to be the official public record."
- Index errors can occur; users should examine the instrument itself rather than rely on index copies.
- **Resale:** material may not be sold or given to third parties.
- **Copyright:** the programs and information are copyright protected, and "Any use of copies deemed to be for profit or not" may lead to legal action.
- No login or registration: access requires only accepting the disclaimer.

These terms are not about automation, but the resale/third-party and for-profit-or-not clauses
matter for a tool that stores and displays record contents. The Disclaimer page linked from the
registry home page (https://www.nhdeeds.org/rockingham-home/) was not separately retrieved.

Other:
- No other terms-of-use text was retrieved for the registry or Tapestry.
- The only stated service limit: "WE WILL NOT search the records for you, interpret documents or give you
  legal advice as to how to prepare documents."
- Payment: "PLEASE NOTE WE CAN ONLY ACCEPT CASH OR CHECK AS A FORM OF PAYMENT."
- No registration requirement was stated for free search. No bulk-data, licence or API option was mentioned.
- Fees pages (recording, copy, postage) exist but amounts were not retrieved.

## Technical access controls and crawl policy

- `https://www.nhdeeds.org/robots.txt`: only `Disallow: /wp-admin/` (admin-ajax allowed) and a sitemap.
  Nothing restricts the public pages. This says nothing about the Ava search application itself, which
  is a separate vendor-hosted app (Fidlar).
- Ava/Tapestry bot protection and terms were not examined.

## Not verified

- The full disclaimer wording (only a summary of the page text was retrieved) and any automation clause.
- Ava (Fidlar) and Tapestry terms of use, rate limits, CAPTCHA behaviour.
- Image-view/print fees in Ava; what Tapestry ("pay as you go") charges.
- VGSI and Rye Assessing terms.
- New Hampshire law on reuse of registry data (needs counsel).

## Sources

- https://www.nhdeeds.org/rockingham-home/
- https://www.nhdeeds.org/rockingham-county-searching-records/
- https://www.nhdeeds.org/rockingham-county-other-nh-registry-list-2/ (Ava disclaimer)
- https://www.nhdeeds.org/rockingham-important-notice/
- https://www.nhdeeds.org/robots.txt
- https://www.town.rye.nh.us/robots.txt
- Project notes: `info/Free Sources for Bridge Loan Research — Rye NH, Madison CT, Mattapoisett MA.md`
